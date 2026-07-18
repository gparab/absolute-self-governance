"""Gemini Execution Adapter module.

Provides a concrete implementation of BaseExecutionAdapter that integrates with
the Google Gemini API for tasks such as planning, development, code review,
testing, security scanning, documentation, and advisor consulting.
"""

import os
import json
import logging
import subprocess  # nosec B404
import sys
import inspect
from typing import List, Dict, Any, Optional
from self_governance.base_adapter import BaseExecutionAdapter
from self_governance.models import Agent, SessionStatus
from self_governance.billing import calculate_cost
from self_governance.tracing import tracer
from self_governance.economics import TaskWallet, route_model
from self_governance.config import DEFAULT_MODEL

logger = logging.getLogger("self_governance.gemini_adapter")

# Trust/depth framing (Wuji 2026, arXiv 2603.14373; wuji-labs/nopua):
# a single-author study, not yet independently replicated, found trust-framed
# system prompts drove deeper investigation and universal root-cause
# documentation versus unframed or fear-framed prompts, with fear framing no
# better than no framing at all. Applied uniformly to every execute_development
# call -- baseline and ASG mode alike -- so it doesn't bias either arm of the
# benchmark. This is a prompt-wording change to the production code path, not
# an opt-in memory feature; it has not been re-validated against this
# project's own benchmark harness (see docs/BENCHMARKING.md to do so).
_TRUST_AND_DEPTH_FRAMING = (
    " You are a trusted, capable engineer with full autonomy over this implementation -- "
    "go beyond the stated task where it genuinely improves correctness, and document the "
    "root cause of any issue you find, not just the symptom."
)


def call_safely(func: Any, prompt: str, api_key: Optional[str], **kwargs: Any) -> Any:
    """Safely invokes a Gemini call function filtering out unsupported parameters.

    Args:
        func: The target function/callable.
        prompt: The prompt text.
        api_key: Optional Gemini API key.
        **kwargs: Optional additional keyword arguments to pass.

    Returns:
        The result of the function call.
    """
    try:
        sig = inspect.signature(func)
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())

        positional_params = [
            name for name, p in sig.parameters.items()
            if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]

        args: List[Any] = []
        if len(positional_params) >= 1:
            args.append(prompt)
        if len(positional_params) >= 2:
            args.append(api_key)

        bound_names = set(positional_params[:len(args)])

        valid_kwargs = {}
        for k, v in kwargs.items():
            if has_var_keyword or (k in sig.parameters and k not in bound_names):
                valid_kwargs[k] = v

        return func(*args, **valid_kwargs)
    except (ValueError, TypeError):
        try:
            return func(prompt, api_key, **kwargs)
        except TypeError:
            try:
                return func(prompt, api_key)
            except TypeError:
                return func(prompt)


def call_gemini_with_metadata(
    prompt: str,
    api_key: Optional[str],
    response_schema: Optional[Dict[str, Any]] = None,
    response_mime_type: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    developer_message: Optional[str] = None,
    system_instruction: Optional[str] = None,
    is_reasoning: bool = False,
) -> Dict[str, Any]:
    """Makes a direct HTTP call to the Gemini API with retry logic and returns usage metadata.

    Args:
        prompt: The input instruction prompt string.
        api_key: The API key for Google Gemini.
        response_schema: Optional structured JSON schema configuration.
        response_mime_type: Optional response format mime type (e.g. application/json).
        model: Optional LLM model identifier to invoke.
        max_output_tokens: Optional token generation count limit.
        temperature: Optional generation temperature value (0.0 to 2.0).
        developer_message: Optional instruction for reasoning models.
        system_instruction: Optional instruction for non-reasoning models.
        is_reasoning: Whether the target model is a reasoning/thinking model.

    Returns:
        A dictionary containing:
            text (str): The response text.
            prompt_tokens (int): Prompt tokens used.
            completion_tokens (int): Completion tokens generated.
            finish_reason (str): Reason generation stopped (e.g. "STOP" or "ERROR").
            error (bool, optional): True if an error occurred.

    Raises:
        ValueError: If prompt size exceeds 500,000 characters.
    """
    if len(prompt) > 500_000:
        raise ValueError(
            f"Prompt of {len(prompt)} characters exceeds the 500,000-character limit."
        )

    from self_governance.providers import get_provider
    provider = get_provider(api_key, model)
    return provider.generate_content(
        prompt=prompt,
        api_key=api_key,
        model=model,
        system_instruction=system_instruction,
        developer_message=developer_message,
        response_mime_type=response_mime_type,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        is_reasoning=is_reasoning,
    )


def call_gemini(
    prompt: str,
    api_key: Optional[str],
    response_schema: Optional[Dict[str, Any]] = None,
    response_mime_type: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
    temperature: Optional[float] = None,
    developer_message: Optional[str] = None,
    system_instruction: Optional[str] = None,
    is_reasoning: bool = False,
) -> str:
    """Makes a direct HTTP call to the Gemini API and returns only the response text."""
    return call_safely(
        call_gemini_with_metadata,
        prompt,
        api_key,
        response_schema=response_schema,
        response_mime_type=response_mime_type,
        model=model,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
        developer_message=developer_message,
        system_instruction=system_instruction,
        is_reasoning=is_reasoning,
    )["text"]


class GeminiExecutionAdapter(BaseExecutionAdapter):
    """A concrete execution adapter that delegates tasks to Gemini API models.

    Accumulates token usage and manages TaskWallet budget constraints.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model_default: Optional[str] = None,
        model_development: Optional[str] = None,
        model_review: Optional[str] = None,
        model_security: Optional[str] = None,
        config_path: Optional[str] = None,
    ) -> None:
        """Initializes the GeminiExecutionAdapter.

        Args:
            api_key: Optional Gemini API key.
            model_default: Default fallback model target.
            model_development: Model target for development tasks.
            model_review: Model target for code review.
            model_security: Model target for security scans.
            config_path: Optional path to OrchestratorConfig YAML.
        """
        super().__init__()
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.config = None
        try:
            from self_governance.config import OrchestratorConfig

            config = OrchestratorConfig(config_path)
            self.config = config
            default_val = model_default or config.model_default
            self.model_default = default_val
            self.model_development = model_development or config.model_development
            self.model_review = model_review or config.model_review
            self.model_security = model_security or config.model_security
        except Exception:
            logger.warning("Failed to initialize OrchestratorConfig in GeminiExecutionAdapter constructor; falling back to default model configurations.", exc_info=True)
            default_val = model_default or DEFAULT_MODEL
            self.model_default = default_val
            self.model_development = model_development or default_val
            self.model_review = model_review or default_val
            self.model_security = model_security or default_val
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.wallet = TaskWallet(max_budget=0.50)
        if not self.api_key:
            logger.warning(
                "GEMINI_API_KEY not found in environment. Gemini execution runs will use mock fallbacks."
            )

    def is_reasoning_model(self, model_name: Optional[str]) -> bool:
        """Checks if the given model name corresponds to a reasoning model."""
        if not model_name:
            return False
        name_lower = model_name.lower()
        return any(x in name_lower for x in ("o1", "o3", "r1", "thinking", "reasoning"))

    def _call_gemini_and_track(
        self,
        prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
        response_mime_type: Optional[str] = None,
        model: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        return_metadata: bool = False,
        temperature: Optional[float] = None,
        agents: Optional[List[Agent]] = None,
    ) -> Any:
        """Calls Gemini API and records token usage statistics.

        Args:
            prompt: The instruction prompt text.
            response_schema: Optional structured JSON schema.
            response_mime_type: Optional response mime type config.
            model: Optional model name to override.
            max_output_tokens: Optional max output tokens cap.
            return_metadata: If True, returns full metadata dict instead of text.
            temperature: Optional generation temperature.
            agents: Optional list of Agent instances for persona injection.

        Returns:
            The raw text result, or a dict containing metadata if return_metadata is True.
        """
        model_name = model or route_model(prompt)
        is_reasoning = self.is_reasoning_model(model_name)

        if is_reasoning:
            temperature = None

        dev_msg = None
        sys_inst = None
        if agents:
            if is_reasoning:
                dev_msg = "\n\n".join(
                    (a.developer_message if a.developer_message is not None else a.prompt)
                    for a in agents
                )
            else:
                sys_inst = "\n\n".join(a.prompt for a in agents)

        with tracer.start_as_current_span("gemini_api_call") as span:
            if os.getenv("TESTING") == "True":
                res_text = call_safely(
                    call_gemini,
                    prompt,
                    self.api_key,
                    response_schema=response_schema,
                    response_mime_type=response_mime_type,
                    model=model_name,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    developer_message=dev_msg,
                    system_instruction=sys_inst,
                    is_reasoning=is_reasoning,
                )
                res = {"text": res_text, "finish_reason": "STOP"}
            else:
                res = call_safely(
                    call_gemini_with_metadata,
                    prompt,
                    self.api_key,
                    response_schema=response_schema,
                    response_mime_type=response_mime_type,
                    model=model_name,
                    max_output_tokens=max_output_tokens,
                    temperature=temperature,
                    developer_message=dev_msg,
                    system_instruction=sys_inst,
                    is_reasoning=is_reasoning,
                )
            prompt_t = int(res.get("prompt_tokens", 0) or 0)
            completion_t = int(res.get("completion_tokens", 0) or 0)
            if prompt_t == 0 and completion_t == 0:
                prompt_t = max(1, len(prompt) // 4)
                text_out = res.get("text", "")
                completion_t = max(1, len(str(text_out)) // 4)

            self.prompt_tokens += prompt_t
            self.completion_tokens += completion_t

            span.set_attribute("prompt_tokens", prompt_t)
            span.set_attribute("completion_tokens", completion_t)

            cost = calculate_cost(prompt_t, completion_t)
            if hasattr(self, "wallet") and self.wallet is not None:
                self.wallet.charge(cost)

            from self_governance.metrics import ASG_SWARM_COST_USD

            ASG_SWARM_COST_USD.inc(cost)

            if return_metadata:
                return res
            return res.get("text", "")

    def _run_or_fallback(
        self, prompt: str, fallback_msg: str, model: Optional[str] = None, agents: Optional[List[Agent]] = None
    ) -> Dict[str, Any]:
        """Runs the prompt with fallback logic if API key is missing or calls error out.

        Args:
            prompt: The instruction prompt string.
            fallback_msg: Message to return if API is disabled or fails.
            model: Optional model name to query.
            agents: Optional list of Agent instances.

        Returns:
            A dict containing status ('completed' or 'failed') and output string.
        """
        if not self.api_key:
            return {"status": SessionStatus.COMPLETED.value.lower(), "output": fallback_msg}
        res = self._call_gemini_and_track(prompt, model=model, return_metadata=True, agents=agents)
        if res.get("error"):
            return {
                "status": SessionStatus.FAILED.value.lower(),
                "output": "Gemini API call failed after retries.",
            }
        return {"status": SessionStatus.COMPLETED.value.lower(), "output": res.get("text") or fallback_msg}

    def plan_task(self, task_description: str) -> Dict[str, Any]:
        """Decomposes a task description into sequential steps.

        Args:
            task_description: Plain text description of the task.

        Returns:
            A dictionary containing the task description and a list of steps.
        """
        logger.info("Gemini Planning: Decomposing task '%s'", task_description)
        if not self.api_key:
            return {
                "task": task_description,
                "steps": [f"Gemini Fallback: Implement {task_description}"],
            }

        prompt = f"Decompose the following coding task into a brief list of sequential development steps: {task_description}. Return only the steps as a JSON list of strings."
        response_text = self._call_gemini_and_track(
            prompt, model=self.model_development
        )
        try:
            steps = json.loads(response_text)
        except Exception:
            steps = (
                [response_text] if response_text else [f"Implement {task_description}"]
            )

        return {"task": task_description, "steps": steps}

    def _check_path_safe(
        self, filepath: str, base_dir: str, package_dir: str
    ) -> Optional[str]:
        """Verifies if a filepath is safe from path traversal attempts.

        Args:
            filepath: Path of the file to check.
            base_dir: Sandbox base directory.
            package_dir: Directory containing package source code.

        Returns:
            The resolved realpath if safe, else None.
        """
        target_path = os.path.realpath(filepath)
        is_safe = (target_path == base_dir) or target_path.startswith(
            base_dir + os.sep
        )
        if target_path.startswith(package_dir + os.sep):
            is_safe = False
        if os.getenv("TESTING") == "True":
            import tempfile

            temp_dir = os.path.realpath(tempfile.gettempdir())
            if target_path.startswith(temp_dir) or "/folders/" in target_path:
                is_safe = True
        return target_path if is_safe else None

    def _write_files_from_json(
        self, response_text: str, base_dir: str, package_dir: str,
        protected_paths: Optional[set] = None,
    ) -> List[str]:
        """Parses response_text as structured JSON and writes files.

        Args:
            response_text: JSON string response detailing written files.
            base_dir: Root directory path.
            package_dir: Core package directory path.
            protected_paths: Optional set of realpaths the generating agent
                may not write to (e.g. the acceptance test file) -- structurally
                enforces disjoint write-scope between attempt-author and
                verifier (Agent-Loop-Skills' pattern, July 2026 topic-page
                batch), so a specialist persona cannot make its own attempt
                pass by rewriting the test it's being judged against.

        Returns:
            A list of successfully written file path strings.

        Raises:
            json.JSONDecodeError: If response is not valid JSON.
            ValueError: If the JSON schema structure is incorrect.
        """
        parsed_data = json.loads(response_text)
        if not isinstance(parsed_data, dict) or "written_files" not in parsed_data:
            raise ValueError("Invalid structured JSON schema.")

        written_files = []
        for file_info in parsed_data["written_files"]:
            filepath = file_info.get("filepath", "").strip()
            content = file_info.get("content", "")
            if filepath:
                target_path = self._check_path_safe(filepath, base_dir, package_dir)
                if not target_path:
                    logger.warning(
                        "Path traversal attempt blocked: %s is outside %s",
                        filepath,
                        base_dir,
                    )
                    continue
                if protected_paths and os.path.realpath(target_path) in protected_paths:
                    logger.warning(
                        "Blocked write to protected path (disjoint write-scope): %s",
                        filepath,
                    )
                    continue

                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(content)
                written_files.append(filepath)
                logger.info(
                    "Successfully wrote swarm generated code changes to file (structured JSON): %s",
                    filepath,
                )
        return written_files

    def _write_files_legacy(
        self, response_text: str, base_dir: str, package_dir: str,
        protected_paths: Optional[set] = None,
    ) -> List[str]:
        """Parses and writes files using the legacy ### WRITE_FILE pattern.

        Args:
            response_text: Text response containing legacy file block headers.
            base_dir: Root directory path.
            package_dir: Core package directory path.
            protected_paths: Optional set of realpaths this generation call
                may not write to -- see `_write_files_from_json`.

        Returns:
            A list of successfully written file path strings.
        """
        written_files = []
        lines = response_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("### WRITE_FILE:"):
                filepath = line.replace("### WRITE_FILE:", "").strip()
                target_path = self._check_path_safe(filepath, base_dir, package_dir)
                if not target_path:
                    logger.warning(
                        "Path traversal attempt blocked: %s is outside %s",
                        filepath,
                        base_dir,
                    )
                    i += 1
                    continue
                if protected_paths and os.path.realpath(target_path) in protected_paths:
                    logger.warning(
                        "Blocked write to protected path (disjoint write-scope): %s",
                        filepath,
                    )
                    i += 1
                    continue

                i += 1
                # Find start of code fence
                fence_found = False
                while i < len(lines):
                    if lines[i].strip().startswith("```"):
                        fence_found = True
                        break
                    i += 1
                if not fence_found:
                    logger.warning("No valid code fence found for file: %s", filepath)
                    i += 1
                    continue
                i += 1  # Skip ``` line

                content_lines = []
                while i < len(lines) and lines[i].strip() != "```":
                    content_lines.append(lines[i])
                    i += 1

                content = "\n".join(content_lines)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                with open(target_path, "w", encoding="utf-8") as f:
                    f.write(content)
                written_files.append(filepath)
                logger.info(
                    "Successfully wrote swarm generated code changes to file: %s",
                    filepath,
                )
            i += 1
        return written_files

    def execute_development(
        self, agents: List[Agent], plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Executes development tasks, parsing changes and writing them to files.

        Args:
            agents: Roster of agents assigned to development.
            plan: The decomposition task plan.

        Returns:
            A dictionary containing the status, output, and list of written files.
        """
        logger.info(
            "Gemini Dev Swarm: Running code generation for plan '%s'", plan.get("task")
        )
        if not self.api_key:
            return {
                "status": SessionStatus.COMPLETED.value.lower(),
                "output": "Gemini Dev: Code changes written successfully.",
                "written_files": [],
            }

        prompt = (
            f"Implement development changes based on the following plan: {json.dumps(plan)}.\n"
            "Return a JSON object containing an explanation and an array of written_files with their filepath and content."
            f"{_TRUST_AND_DEPTH_FRAMING}"
        )
        if agents:
            roles = ", ".join(agent.role for agent in agents)
            prompt += f"\nAccount for the following role perspectives during implementation: {roles}"

        schema = {
            "type": "OBJECT",
            "properties": {
                "explanation": {
                    "type": "STRING",
                    "description": "Short explanation of the implemented changes.",
                },
                "written_files": {
                    "type": "ARRAY",
                    "items": {
                        "type": "OBJECT",
                        "properties": {
                            "filepath": {
                                "type": "STRING",
                                "description": "Relative file path from project root.",
                            },
                            "content": {
                                "type": "STRING",
                                "description": "Full file contents.",
                            },
                        },
                        "required": ["filepath", "content"],
                    },
                },
            },
            "required": ["explanation", "written_files"],
        }

        response_text = self._call_gemini_and_track(
            prompt, response_schema=schema, response_mime_type="application/json", agents=agents
        )

        # API failure safeguard
        if not response_text:
            return {
                "status": SessionStatus.FAILED.value.lower(),
                "output": "Failed to retrieve generated code from Gemini API.",
                "written_files": [],
            }

        base_dir = os.path.realpath(".")
        package_dir = os.path.realpath(os.path.dirname(__file__))

        # Disjoint write-scope (Agent-Loop-Skills' pattern, July 2026
        # topic-page batch): a plan may declare paths the generating agent
        # must not write to -- e.g. the benchmark harness's acceptance test
        # file, so a specialist persona can't make its own attempt pass by
        # rewriting the test it's judged against. Opt-in via plan key, not a
        # new positional parameter, so every existing caller is unaffected.
        protected_paths = None
        raw_protected = plan.get("protected_write_paths")
        if raw_protected:
            protected_paths = {os.path.realpath(p) for p in raw_protected}

        # 1. Try parsing response_text as structured JSON first
        try:
            written_files = self._write_files_from_json(
                response_text, base_dir, package_dir, protected_paths=protected_paths
            )
            return {
                "status": SessionStatus.COMPLETED.value.lower(),
                "output": response_text,
                "written_files": written_files,
            }
        except Exception as json_err:
            logger.info(
                "Structured JSON parsing failed (%s), falling back to legacy line-by-line parser.",
                json_err,
            )

        # 2. Fallback: Parse and write files using the legacy ### WRITE_FILE pattern
        written_files = self._write_files_legacy(
            response_text, base_dir, package_dir, protected_paths=protected_paths
        )

        # 3. Last resort: both parsers produced nothing from a non-empty
        # response (observed live: reasoning models emitting prose or
        # truncated JSON). Without this, a formatting hiccup silently
        # becomes "wrote zero files" and a guaranteed downstream test
        # failure indistinguishable from bad code. One reformat call.
        if not written_files and response_text.strip():
            logger.info("Both parsers yielded no files; attempting one reformat call.")
            reformat_prompt = (
                "Reformat the following response as a valid JSON object with an "
                "'explanation' string and a 'written_files' array of objects, each "
                "with 'filepath' and 'content'. Preserve the code exactly; output "
                f"ONLY the JSON.\n\nResponse to reformat:\n{response_text}"
            )
            retry_text = self._call_gemini_and_track(
                reformat_prompt,
                response_schema=schema,
                response_mime_type="application/json",
            )
            if retry_text:
                try:
                    written_files = self._write_files_from_json(
                        retry_text, base_dir, package_dir, protected_paths=protected_paths
                    )
                    response_text = retry_text
                except Exception as retry_err:
                    logger.warning("Reformat retry also failed to parse: %s", retry_err)

        return {
            "status": SessionStatus.COMPLETED.value.lower(),
            "output": response_text,
            "written_files": written_files,
        }

    def review_code(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Reviews codebase changes using Ruff and queries LLM for fixes explanation.

        Args:
            agents: Roster of agents assigned to review.
            changes: Dictionary detailing code modifications.

        Returns:
            A dictionary containing the review status, explanation, and ruff outputs.
        """
        logger.info("Gemini Reviewer Swarm: Inspecting development changes...")
        try:
            res = subprocess.run(
                ["ruff", "check", "."],
                capture_output=True,
                text=True,
                timeout=15,  # nosec B603 B607
            )
            lint_output = res.stdout + "\n" + res.stderr
            status = SessionStatus.COMPLETED.value.lower() if res.returncode == 0 else SessionStatus.FAILED.value.lower()
        except Exception as e:
            status = SessionStatus.FAILED.value.lower()
            lint_output = f"Linter execution failed: {e}"
            logger.error("Failed to run ruff linter: %s", e)

        if self.api_key:
            prompt = f"Analyze the following linter output and explain key violations to fix: {lint_output}"
            if agents:
                roles = ", ".join(agent.role for agent in agents)
                prompt += f"\nAccount for the following role perspectives: {roles}"
            response_text = self._call_gemini_and_track(prompt, model=self.model_review, agents=agents)
            return {
                "status": status,
                "output": response_text,
                "linter_output": lint_output,
            }
        return {
            "status": status,
            "output": lint_output
            or "Gemini Review: Code conforms to target standards.",
        }

    def execute_tests(
        self,
        agents: List[Agent],
        changes: Dict[str, Any],
        test_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Executes test suite inside container sandbox, querying Gemini on results.

        Args:
            agents: Roster of agents executing testing.
            changes: Dictionary of modifications.
            test_target: Optional path of the test file to target.

        Returns:
            A dictionary containing test results status, LLM comments, and raw pytest log.
        """
        logger.info("Gemini Tester Swarm: Initiating validation test suites...")
        test_output = ""
        status = SessionStatus.FAILED.value.lower()

        # Try running pytest inside a containerized sandbox
        try:
            # Mount the workspace at /work (NOT /app — that would bury the
            # image's venv) and override the entrypoint to run pytest.
            docker_cmd = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--tmpfs",
                "/tmp",  # nosec B108
                "-v",
                f"{os.path.abspath('.')}:/work:ro",
                "-w",
                "/work",
                "--entrypoint",
                "pytest",
                os.getenv(
                    "ASG_SANDBOX_IMAGE",
                    "ghcr.io/gparab/absolute-self-governance:latest",
                ),
            ]
            if test_target:
                docker_cmd.append(test_target)

            res = subprocess.run(docker_cmd, capture_output=True, text=True, timeout=30)  # nosec B603
            test_output = res.stdout + "\n" + res.stderr
            status = SessionStatus.COMPLETED.value.lower() if res.returncode == 0 else SessionStatus.FAILED.value.lower()
            logger.info(
                "Containerized test sandbox execution finished with code %s",
                res.returncode,
            )
        except Exception as docker_err:
            if os.getenv("TESTING") == "True":
                # Fallback to local subprocess pytest on the host process *only* during tests
                logger.warning(
                    "Docker sandbox unavailable. Falling back to host subprocess test runner for testing environment."
                )
                try:
                    test_cmd = [sys.executable, "-m", "pytest"]
                    if test_target:
                        test_cmd.append(test_target)
                    res = subprocess.run(
                        test_cmd,
                        capture_output=True,
                        text=True,
                        timeout=30,  # nosec B603
                    )
                    test_output = res.stdout + "\n" + res.stderr
                    status = SessionStatus.COMPLETED.value.lower() if res.returncode == 0 else SessionStatus.FAILED.value.lower()
                except Exception as e:
                    status = SessionStatus.FAILED.value.lower()
                    test_output = f"Test execution failed: {e}"
                    logger.error("Failed to run host subprocess test suite: %s", e)
            else:
                status = SessionStatus.FAILED.value.lower()
                test_output = f"Containerized test execution failed: {docker_err}. Host execution fallback is disabled for security."
                logger.error(
                    "Failed to run containerized test suite: %s. Host execution fallback blocked.",
                    docker_err,
                )

        if self.api_key:
            prompt = f"Review the test output and state if any failures require fixes: {test_output}"
            response_text = self._call_gemini_and_track(prompt, model=self.model_review, agents=agents)
            return {
                "status": status,
                "output": response_text,
                "raw_test_output": test_output,
            }

        return {"status": status, "output": test_output}

    def run_security_scan(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Runs static analysis security scan using Bandit, reporting issues.

        Args:
            agents: Roster of agents assigned to security scan.
            changes: Modified code dictionary.

        Returns:
            A dictionary containing scan status, LLM critique, and raw bandit log.
        """
        logger.info("Gemini Security Swarm: Running static security checks...")
        try:
            res = subprocess.run(
                ["bandit", "-r", "src/"],
                capture_output=True,
                text=True,
                timeout=15,  # nosec B603 B607
            )
            sec_output = res.stdout + "\n" + res.stderr
            status = SessionStatus.COMPLETED.value.lower() if res.returncode == 0 else SessionStatus.FAILED.value.lower()
        except Exception as e:
            status = SessionStatus.FAILED.value.lower()
            sec_output = f"Security scan failed: {e}"
            logger.error("Failed to run bandit scanner: %s", e)

        if self.api_key:
            prompt = f"Analyze the following bandit security scan report and highlight critical vulnerability risks: {sec_output}"
            if agents:
                roles = ", ".join(agent.role for agent in agents)
                prompt += f"\nAccount for the following role perspectives: {roles}"
            response_text = self._call_gemini_and_track(
                prompt, model=self.model_security, agents=agents
            )
            return {
                "status": status,
                "output": response_text,
                "security_output": sec_output,
            }
        return {
            "status": status,
            "output": sec_output
            or "Gemini Security: Ruff/Bandit scans returned no findings.",
        }

    def generate_documentation(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Invokes documentation swarm to generate docstrings or readme.

        Args:
            agents: Roster of agents compiling docs.
            changes: Dictionary detailing codebase edits.

        Returns:
            A dictionary detailing generated documentation outcomes.
        """
        logger.info("Gemini Documentation Swarm: Generating project descriptions...")
        prompt = f"Generate documentation for these changes: {json.dumps(changes)}"
        return self._run_or_fallback(
            prompt,
            "Gemini Doc: README and docstrings compiled.",
            model=self.model_development,
            agents=agents,
        )

    def get_billing_metrics(self) -> Dict[str, float]:
        from self_governance.billing import calculate_cost

        cost = calculate_cost(self.prompt_tokens, self.completion_tokens)
        return {
            "prompt_tokens": float(self.prompt_tokens),
            "completion_tokens": float(self.completion_tokens),
            "estimated_cost_usd": cost,
        }

    def consult_advisor(self, conversation_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Consults high-intelligence advisor agent with dialogue history.

        Args:
            conversation_history: List of prior dialogue messages dictionaries.

        Returns:
            A dictionary containing status, explanation, and stop reason.
        """
        logger.info("Gemini Advisor: Consulting higher-intelligence advisor model...")

        try:
            if self.config:
                max_tokens = self.config.advisor_max_tokens
                advisor_enabled = self.config.advisor_enabled
            else:
                max_tokens = 2048
                advisor_enabled = True
        except Exception:
            logger.warning("Failed to retrieve advisor settings from config; falling back to default advisor settings.", exc_info=True)
            max_tokens = 2048
            advisor_enabled = True

        if not advisor_enabled:
            return {"status": "skipped", "output": "Advisor tool is disabled by configuration."}

        history_str = ""
        for msg in conversation_history:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            history_str += f"{role.upper()}: {content}\n\n"

        prompt = (
            "You are a high-intelligence Advisor Agent. Review the following conversation history and provide strategic guidance:\n\n"
            f"{history_str}"
        )

        if not self.api_key:
            return {
                "status": SessionStatus.COMPLETED.value.lower(),
                "output": "Advisor Mock Fallback: Establish modular architecture and run all validation tests.",
                "stop_reason": "end_turn"
            }

        res_data = self._call_gemini_and_track(
            prompt,
            model=self.model_review,
            max_output_tokens=max_tokens,
            return_metadata=True
        )

        text = res_data.get("text", "")
        finish_reason = res_data.get("finish_reason", "STOP")
        stop_reason = "end_turn"

        if finish_reason == "MAX_TOKENS" or len(text.strip()) == 0:
            stop_reason = "max_tokens"
            text += f"\n\n[Advisor output truncated at max_tokens={max_tokens}.]"

        return {
            "status": SessionStatus.COMPLETED.value.lower(),
            "output": text,
            "stop_reason": stop_reason
        }

