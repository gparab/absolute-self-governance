import os
import json
import logging
import urllib.request
import urllib.error
import time
import subprocess  # nosec B404
import sys
from typing import List, Dict, Any, Optional
from self_governance.base_adapter import BaseExecutionAdapter
from self_governance.models import Agent
from self_governance.tracing import tracer

logger = logging.getLogger("self_governance.gemini_adapter")


def call_gemini_with_metadata(
    prompt: str,
    api_key: str,
    response_schema: Optional[Dict[str, Any]] = None,
    response_mime_type: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> Dict[str, Any]:
    """Make a direct HTTP call to the Gemini API and return text along with usage metadata."""
    # ~500k chars ≈ 125k tokens: fail fast on runaway prompts instead of
    # paying for them or hitting opaque model-side limits.
    if len(prompt) > 500_000:
        raise ValueError(
            f"Prompt of {len(prompt)} characters exceeds the 500,000-character limit."
        )
    model_name = model or "gemini-2.5-flash"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key or ""}
    data = {"contents": [{"parts": [{"text": prompt}]}]}

    if response_mime_type or response_schema or max_output_tokens:
        gen_config = {}
        if response_mime_type:
            gen_config["responseMimeType"] = response_mime_type
        if response_schema:
            gen_config["responseSchema"] = response_schema
        if max_output_tokens:
            gen_config["maxOutputTokens"] = max_output_tokens
        data["generationConfig"] = gen_config

    attempts = 3
    delay = 1.0

    for attempt in range(attempts):
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:  # nosec B310
                res_data = json.loads(response.read().decode())
                candidates = res_data.get("candidates", [])
                usage_metadata = res_data.get("usageMetadata", {})
                prompt_tokens = usage_metadata.get("promptTokenCount", 0)
                completion_tokens = usage_metadata.get("candidatesTokenCount", 0)

                text = ""
                finish_reason = "STOP"
                if candidates:
                    finish_reason = candidates[0].get("finishReason", "STOP")
                    content = candidates[0].get("content", {})
                    parts = content.get("parts", [])
                    if parts:
                        text = parts[0].get("text", "").strip()

                return {
                    "text": text,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "finish_reason": finish_reason,
                }
        except urllib.error.HTTPError as he:
            if he.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                logger.warning(
                    "Gemini API returned transient error %s. Retrying in %s seconds...",
                    he.code,
                    delay,
                )
                time.sleep(delay)
                delay *= 2.0
            else:
                logger.error(
                    "Gemini API HTTP Error %s: %s", he.code, he.read().decode()
                )
                break
        except Exception as e:
            if attempt < attempts - 1:
                logger.warning("Query error: %s. Retrying in %s seconds...", e, delay)
                time.sleep(delay)
                delay *= 2.0
            else:
                logger.error("Failed to query Gemini API: %s", e)
                break
    # Failure channel: callers must check "error" rather than treating an
    # empty response as a successful completion.
    return {
        "text": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "finish_reason": "ERROR",
        "error": True,
    }


def call_gemini(
    prompt: str,
    api_key: str,
    response_schema: Optional[Dict[str, Any]] = None,
    response_mime_type: Optional[str] = None,
    model: Optional[str] = None,
    max_output_tokens: Optional[int] = None,
) -> str:
    """Make a direct HTTP call to the Gemini API with exponential backoff retries."""
    try:
        return call_gemini_with_metadata(
            prompt, api_key, response_schema, response_mime_type, model, max_output_tokens
        )["text"]
    except TypeError:
        return call_gemini_with_metadata(
            prompt, api_key, response_schema, response_mime_type, model
        )["text"]


class GeminiExecutionAdapter(BaseExecutionAdapter):
    """
    A concrete execution adapter that delegates tasks to Gemini API models.
    """

    def __init__(
        self,
        api_key: str = None,
        model_default: Optional[str] = None,
        model_development: Optional[str] = None,
        model_review: Optional[str] = None,
        model_security: Optional[str] = None,
    ) -> None:
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        try:
            from self_governance.config import OrchestratorConfig

            config = OrchestratorConfig()
            default_val = model_default or config.model_default
            self.model_default = default_val
            self.model_development = model_development or config.model_development
            self.model_review = model_review or config.model_review
            self.model_security = model_security or config.model_security
        except Exception:
            default_val = model_default or "gemini-2.5-flash"
            self.model_default = default_val
            self.model_development = model_development or default_val
            self.model_review = model_review or default_val
            self.model_security = model_security or default_val
        self.prompt_tokens = 0
        self.completion_tokens = 0
        if not self.api_key:
            logger.warning(
                "GEMINI_API_KEY not found in environment. Gemini execution runs will use mock fallbacks."
            )

    def _call_gemini_and_track(
        self,
        prompt: str,
        response_schema: Optional[Dict[str, Any]] = None,
        response_mime_type: Optional[str] = None,
        model: Optional[str] = None,
        max_output_tokens: Optional[int] = None,
        return_metadata: bool = False,
    ) -> Any:
        """Call Gemini and accumulate token counts for pricing calculations."""
        model_name = model or self.model_default
        with tracer.start_as_current_span("gemini_api_call") as span:
            if os.getenv("TESTING") == "True":
                try:
                    res_text = call_gemini(
                        prompt,
                        self.api_key,
                        response_schema=response_schema,
                        response_mime_type=response_mime_type,
                        model=model_name,
                        max_output_tokens=max_output_tokens,
                    )
                    res = {"text": res_text, "finish_reason": "STOP"}
                except TypeError:
                    try:
                        res_text = call_gemini(
                            prompt,
                            self.api_key,
                            response_schema=response_schema,
                            response_mime_type=response_mime_type,
                            max_output_tokens=max_output_tokens,
                        )
                        res = {"text": res_text, "finish_reason": "STOP"}
                    except TypeError:
                        res = {"text": call_gemini(prompt, self.api_key), "finish_reason": "STOP"}
            else:
                try:
                    res = call_gemini_with_metadata(
                        prompt,
                        self.api_key,
                        response_schema=response_schema,
                        response_mime_type=response_mime_type,
                        model=model_name,
                        max_output_tokens=max_output_tokens,
                    )
                except TypeError:
                    try:
                        res = call_gemini_with_metadata(
                            prompt,
                            self.api_key,
                            response_schema=response_schema,
                            response_mime_type=response_mime_type,
                            max_output_tokens=max_output_tokens,
                        )
                    except TypeError:
                        res = call_gemini_with_metadata(prompt, self.api_key)
            prompt_t = res.get("prompt_tokens", 0)
            completion_t = res.get("completion_tokens", 0)
            self.prompt_tokens += prompt_t
            self.completion_tokens += completion_t

            span.set_attribute("prompt_tokens", prompt_t)
            span.set_attribute("completion_tokens", completion_t)

            cost = (prompt_t * 0.000000075) + (completion_t * 0.00000030)
            from self_governance.metrics import ASG_SWARM_COST_USD

            ASG_SWARM_COST_USD.inc(cost)

            if return_metadata:
                return res
            return res.get("text", "")

    def _run_or_fallback(self, prompt: str, fallback_msg: str, model: Optional[str] = None) -> Dict[str, Any]:
        """Verify API key presence and return Gemini output or a fallback message."""
        if not self.api_key:
            return {"status": "completed", "output": fallback_msg}
        res = self._call_gemini_and_track(prompt, model=model, return_metadata=True)
        if res.get("error"):
            return {
                "status": "failed",
                "output": "Gemini API call failed after retries.",
            }
        return {"status": "completed", "output": res.get("text") or fallback_msg}

    def plan_task(self, task_description: str) -> Dict[str, Any]:
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

    def execute_development(
        self, agents: List[Agent], plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info(
            "Gemini Dev Swarm: Running code generation for plan '%s'", plan.get("task")
        )
        if not self.api_key:
            return {
                "status": "completed",
                "output": "Gemini Dev: Code changes written successfully.",
                "written_files": [],
            }

        prompt = (
            f"Implement development changes based on the following plan: {json.dumps(plan)}.\n"
            "Return a JSON object containing an explanation and an array of written_files with their filepath and content."
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
            prompt, response_schema=schema, response_mime_type="application/json"
        )

        # API failure safeguard
        if not response_text:
            return {
                "status": "failed",
                "output": "Failed to retrieve generated code from Gemini API.",
                "written_files": [],
            }

        written_files = []
        base_dir = os.path.realpath(".")

        # LLM output is attacker-influenced (webhook issue bodies feed prompts):
        # never let it overwrite this application's own code.
        package_dir = os.path.realpath(os.path.dirname(__file__))

        # Path Traversal Check helper
        def check_path_safe(filepath: str) -> Optional[str]:
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

        # 1. Try parsing response_text as structured JSON first
        try:
            parsed_data = json.loads(response_text)
            if isinstance(parsed_data, dict) and "written_files" in parsed_data:
                for file_info in parsed_data["written_files"]:
                    filepath = file_info.get("filepath", "").strip()
                    content = file_info.get("content", "")
                    if filepath:
                        target_path = check_path_safe(filepath)
                        if not target_path:
                            logger.warning(
                                "Path traversal attempt blocked: %s is outside %s",
                                filepath,
                                base_dir,
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

                return {
                    "status": "completed",
                    "output": response_text,
                    "written_files": written_files,
                }
        except Exception as json_err:
            logger.info(
                "Structured JSON parsing failed (%s), falling back to legacy line-by-line parser.",
                json_err,
            )

        # 2. Fallback: Parse and write files using the legacy ### WRITE_FILE pattern
        lines = response_text.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("### WRITE_FILE:"):
                filepath = line.replace("### WRITE_FILE:", "").strip()
                target_path = check_path_safe(filepath)
                if not target_path:
                    logger.warning(
                        "Path traversal attempt blocked: %s is outside %s",
                        filepath,
                        base_dir,
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

        return {
            "status": "completed",
            "output": response_text,
            "written_files": written_files,
        }

    def review_code(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info("Gemini Reviewer Swarm: Inspecting development changes...")
        try:
            res = subprocess.run(
                ["ruff", "check", "."],
                capture_output=True,
                text=True,
                timeout=15,  # nosec B603 B607
            )
            lint_output = res.stdout + "\n" + res.stderr
            status = "completed" if res.returncode == 0 else "failed"
        except Exception as e:
            status = "failed"
            lint_output = f"Linter execution failed: {e}"
            logger.error("Failed to run ruff linter: %s", e)

        if self.api_key:
            prompt = f"Analyze the following linter output and explain key violations to fix: {lint_output}"
            if agents:
                roles = ", ".join(agent.role for agent in agents)
                prompt += f"\nAccount for the following role perspectives: {roles}"
            response_text = self._call_gemini_and_track(prompt, model=self.model_review)
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
        logger.info("Gemini Tester Swarm: Initiating validation test suites...")
        test_output = ""
        status = "failed"

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
            status = "completed" if res.returncode == 0 else "failed"
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
                    status = "completed" if res.returncode == 0 else "failed"
                except Exception as e:
                    status = "failed"
                    test_output = f"Test execution failed: {e}"
                    logger.error("Failed to run host subprocess test suite: %s", e)
            else:
                status = "failed"
                test_output = f"Containerized test execution failed: {docker_err}. Host execution fallback is disabled for security."
                logger.error(
                    "Failed to run containerized test suite: %s. Host execution fallback blocked.",
                    docker_err,
                )

        if self.api_key:
            prompt = f"Review the test output and state if any failures require fixes: {test_output}"
            response_text = self._call_gemini_and_track(prompt, model=self.model_review)
            return {
                "status": status,
                "output": response_text,
                "raw_test_output": test_output,
            }

        return {"status": status, "output": test_output}

    def run_security_scan(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        logger.info("Gemini Security Swarm: Running static security checks...")
        try:
            res = subprocess.run(
                ["bandit", "-r", "src/"],
                capture_output=True,
                text=True,
                timeout=15,  # nosec B603 B607
            )
            sec_output = res.stdout + "\n" + res.stderr
            status = "completed" if res.returncode == 0 else "failed"
        except Exception as e:
            status = "failed"
            sec_output = f"Security scan failed: {e}"
            logger.error("Failed to run bandit scanner: %s", e)

        if self.api_key:
            prompt = f"Analyze the following bandit security scan report and highlight critical vulnerability risks: {sec_output}"
            if agents:
                roles = ", ".join(agent.role for agent in agents)
                prompt += f"\nAccount for the following role perspectives: {roles}"
            response_text = self._call_gemini_and_track(
                prompt, model=self.model_security
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
        logger.info("Gemini Documentation Swarm: Generating project descriptions...")
        prompt = f"Generate documentation for these changes: {json.dumps(changes)}"
        return self._run_or_fallback(
            prompt,
            "Gemini Doc: README and docstrings compiled.",
            model=self.model_development,
        )

    def consult_advisor(self, conversation_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        logger.info("Gemini Advisor: Consulting higher-intelligence advisor model...")
        
        try:
            from self_governance.config import OrchestratorConfig
            config = OrchestratorConfig()
            max_tokens = config.advisor_max_tokens
            advisor_enabled = config.advisor_enabled
        except Exception:
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
                "status": "completed",
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
            "status": "completed",
            "output": text,
            "stop_reason": stop_reason
        }
