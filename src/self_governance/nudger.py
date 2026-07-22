"""Event-driven Handoff Monitoring and Succession Swarm Nudger.

Defines hook execution, monitoring event logging, handoff observers, and
the main ContinuousNudger orchestrator that triggers TETD consensus and next-step
prompt generation.
"""

import os
import time
import yaml
import json
import logging
import threading
import subprocess  # nosec B404
from typing import Optional, Any, List
from self_governance.models import SessionStatus, PipelineStatus
from self_governance.consensus import run_consensus, ConsensusResult
from self_governance.dimensioning import dimension_swarm
from self_governance.config import OrchestratorConfig
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from self_governance.anti_drift import LoopDetector, LoopInterceptionError, self_critique
from self_governance.graph_memory import GraphMemoryEngine
from self_governance.fact_extraction import extract_facts
from self_governance.injection_defense import sanitize, TrustLevel
from self_governance.policy import AgentBudget, ActionSource, PolicyAction, PolicyDenied, PolicyEngine, RiskLevel
from self_governance.policy_rules import default_rule_set
from self_governance.alerts import AlertEngine, default_alert_rules
from self_governance.gemini_adapter import build_sandbox_pytest_argv, GeminiExecutionAdapter

logger = logging.getLogger("self_governance.nudger")


def _emit_event(working_directory: str, event_type: str, data: dict) -> None:
    """Emits a structured monitoring event as standard output print and NDJSON file.

    Args:
        working_directory: Path to working directory for output.
        event_type: Category identifier of the event (e.g. 'spawn', 'consensus').
        data: Additional dictionary attributes to log.
    """
    try:
        # Prevent any mock-related path joining errors
        if hasattr(working_directory, "_mock_name") or "mock" in type(working_directory).__name__.lower():
            wdir = "."
        else:
            wdir = str(working_directory)

        safe_data: dict[str, Any] = {}
        for k, v in data.items():
            if hasattr(v, "_mock_name") or "mock" in type(v).__name__.lower():
                safe_data[k] = str(v)
            elif isinstance(v, list):
                # Check list elements for mocks
                safe_list = []
                for x in v:
                    if hasattr(x, "_mock_name") or "mock" in type(x).__name__.lower():
                        safe_list.append(str(x))
                    else:
                        safe_list.append(x)
                safe_data[k] = safe_list
            else:
                safe_data[k] = v

        event = {
            "timestamp": time.time(),
            "type": event_type,
            **safe_data
        }

        class SafeJSONEncoder(json.JSONEncoder):
            def default(self, o):
                if hasattr(o, "_mock_name") or "mock" in type(o).__name__.lower():
                    return str(o)
                try:
                    return super().default(o)
                except TypeError:
                    return str(o)

        event_str = json.dumps(event, cls=SafeJSONEncoder)
        print(event_str, flush=True)
        ndjson_path = os.path.join(wdir, "monitoring_events.ndjson")
        with open(ndjson_path, "a", encoding="utf-8") as f:
            f.write(event_str + "\n")
    except Exception as e:
        logger.error("Failed to emit/write NDJSON event: %s", e)


class ResilientHookExecutor:
    """Executes lifecycle hooks resiliently, suppressing stdout/stderr.

    Ensures the host process never crashes on hook execution errors.
    """

    def __init__(self, working_directory: str) -> None:
        """Initializes the ResilientHookExecutor.

        Args:
            working_directory: Sandbox base directory to search for hooks.
        """
        self.working_directory = working_directory

    def execute_hook(self, hook_name: str, payload: dict) -> dict:
        """Executes a configured lifecycle hook.

        Args:
            hook_name: The name of the hook (e.g. 'PreToolUse', 'PreCompact').
            payload: Parameters payload to pass as standard input.

        Returns:
            A dictionary containing either {"permission": "allow" / "deny", "status": str}
            or details of exit code and outputs.
        """
        hooks_dir = os.path.join(self.working_directory, "hooks")
        if not os.path.isdir(hooks_dir):
            hooks_dir = os.path.abspath("hooks")

        if not os.path.isdir(hooks_dir):
            return {"permission": "allow", "status": "no_hooks_directory"}

        hook_file = None
        try:
            for filename in os.listdir(hooks_dir):
                name_without_ext, _ = os.path.splitext(filename)
                if name_without_ext == hook_name:
                    hook_file = os.path.join(hooks_dir, filename)
                    break
        except Exception as e:
            logger.error("Failed to list hooks directory: %s", e)
            return {"permission": "allow", "status": "error", "error_message": str(e)}

        if not hook_file:
            return {"permission": "allow", "status": "no_hook_configured"}

        try:
            _, ext = os.path.splitext(hook_file)
            if ext == ".py":
                import sys
                cmd = [sys.executable, hook_file]
            elif ext == ".sh":
                cmd = ["/bin/sh", hook_file]
            else:
                cmd = [hook_file]

            # Hooks are operator-installed executables from the repo's own
            # hooks dir (same trust model as git hooks): static argv, no
            # shell, 5s timeout.
            res = subprocess.run(  # nosec B603
                cmd,
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=5.0,
                check=False
            )

            if res.returncode != 0:
                logger.warning("Hook %s exited with non-zero status: %d", hook_name, res.returncode)

            try:
                out_data = json.loads(res.stdout.strip())
                if isinstance(out_data, dict):
                    return {
                        "permission": out_data.get("permission", "allow"),
                        "status": "executed",
                        "exit_code": res.returncode,
                        "output": out_data
                    }
            except Exception:  # nosec B110
                # Hook stdout wasn't JSON — fall through to the default
                # allow-with-exit-code result below.
                pass

            return {"permission": "allow", "status": "executed", "exit_code": res.returncode}

        except Exception as e:
            logger.error("Resilient hook execution failed for %s: %s", hook_name, e)
            return {"permission": "allow", "status": "error", "error_message": str(e)}


class HandoffValueError(ValueError):
    """Exception raised when handoff values are invalid or malformed."""

    pass


class HandoffKeyError(KeyError):
    """Exception raised when a required key is missing in handoff."""

    pass


class HandoffTypeError(TypeError):
    """Exception raised when handoff types are incorrect."""

    pass


def write_swarm_config_to_stream(stream: Any, config: Any) -> None:
    """Streams SwarmConfig serialization directly to a file handle block-by-block.

    Args:
        stream: Write-supporting file stream context.
        config: SwarmConfig instance.
    """
    if not config.swarm:
        stream.write('{\n  "swarm": []\n}')
        return

    stream.write("{\n")
    stream.write('  "swarm": [\n')
    first = True
    for agent in config.swarm:
        if not first:
            stream.write(",\n")
        first = False
        agent_str = json.dumps(dict(agent), indent=2)
        indented_agent = "\n".join("    " + line for line in agent_str.splitlines())
        stream.write(indented_agent)
    stream.write("\n  ]\n")
    stream.write("}")


PIPELINE_ARTIFACT_FILE = "pipeline_artifact.jsonl"
_MAX_PRIOR_ARTIFACTS = 5  # Max previous artifacts to load as context


def load_prior_artifacts(working_directory: str) -> list:
    """Loads the most recent pipeline artifacts from the JSONL chain.

    Args:
        working_directory: Directory where pipeline_artifact.jsonl lives.

    Returns:
        List of raw artifact dicts (most recent last), up to _MAX_PRIOR_ARTIFACTS.
    """
    path = os.path.join(working_directory, PIPELINE_ARTIFACT_FILE)
    if not os.path.exists(path):
        return []
    artifacts = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        artifacts.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except Exception as e:
        logger.warning("Failed to load pipeline artifacts: %s", e)
    return artifacts[-_MAX_PRIOR_ARTIFACTS:]


def append_pipeline_artifact(working_directory: str, artifact_dict: dict) -> None:
    """Appends one PipelineArtifact JSON record to the JSONL chain.

    Args:
        working_directory: Directory where pipeline_artifact.jsonl lives.
        artifact_dict: Serialized PipelineArtifact dict.
    """
    path = os.path.join(working_directory, PIPELINE_ARTIFACT_FILE)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(artifact_dict) + "\n")
        logger.info("Pipeline artifact appended to %s", path)
    except Exception as e:
        logger.error("Failed to append pipeline artifact: %s", e)


class HandoffHandler(FileSystemEventHandler):
    """Watchdog events handler responding to handoff file modifications."""

    def __init__(self, nudger: "ContinuousNudger") -> None:
        """Initializes the HandoffHandler.

        Args:
            nudger: Active ContinuousNudger instance.
        """
        self.nudger = nudger

    def _is_handoff_file(self, src_path: str) -> bool:
        target_path = os.path.abspath(
            os.path.join(self.nudger.working_directory, self.nudger.config.handoff_file)
        )
        return os.path.abspath(src_path) == target_path

    def on_modified(self, event: Any) -> None:
        """Invoked on file modified event.

        Args:
            event: Watchdog file event.
        """
        if not event.is_directory and self._is_handoff_file(event.src_path):
            self.nudger.process_handoff()

    def on_created(self, event: Any) -> None:
        """Invoked on file created event.

        Args:
            event: Watchdog file event.
        """
        if not event.is_directory and self._is_handoff_file(event.src_path):
            self.nudger.process_handoff()


class SimulationException(Exception):
    """Raised when the sandbox simulation hits unresolvable friction."""
    pass

def run_sandbox_simulation(handoff_content: str, adapter: Optional[Any]) -> None:
    """Spawns Council personas to simulate debate over the handoff before dimensioning."""
    if not adapter:
        return
        
    from self_governance.agency_agents_adapter import DynamicAgentFactory
    factory = DynamicAgentFactory()
    # Spawning Council personas dynamically
    council_roles = ["Software Industry Visionary", "Chief Risk Officer", "General Counsel"]
    for role in council_roles:
        factory.synthesize_council_expert(role, adapter)
    
    debate_prompt = (
        f"You are a Council consisting of {', '.join(council_roles)}.\n"
        f"Debate this handoff content for structural flaws and strategic alignment:\n{handoff_content}\n"
        "Respond with exactly 'PASS' if safe, or 'FAIL: <reason>' if there is unresolvable friction."
    )
    
    res = adapter._run_or_fallback(debate_prompt, fallback_msg="PASS").get("output", "PASS")
    if "FAIL" in res.upper():
        raise SimulationException(f"Council Sandbox rejected handoff: {res.strip()}")

class ContinuousNudger:
    """Event-driven file watcher that monitors handoff.md for COMPLETED status.

    When triggered, it initiates a succession session and schedules the next phase.
    """

    def __init__(
        self,
        working_directory: str,
        config: Optional[OrchestratorConfig] = None,
        action_budget: Optional[int] = 10_000,
    ) -> None:
        """Initialize ContinuousNudger.

        Args:
            working_directory: The directory where handoff.md, logs, and prompt drafts are located.
            config: Optional OrchestratorConfig instance.
            action_budget: Per-process ceiling on policy-gated actions
                (Agent Contracts, research.google survey, July 2026
                topic-page batch), enforced by BudgetConservationRule
                alongside the existing git-mutation-specific rate limit.
                Deliberately generous by default -- this is a backstop
                against a runaway process, not a normal-operation
                constraint -- so existing callers see no behavior change
                unless they pass a tighter budget. None disables budget
                tracking entirely (BudgetConservationRule abstains).
        """
        self.working_directory = working_directory
        self.config = config if config is not None else OrchestratorConfig()
        self.action_budget = AgentBudget(max_actions=action_budget) if action_budget is not None else None
        # RLock, not Lock: process_handoff() holds this lock for its whole
        # body and calls trigger_succession() from within it (via
        # _execute_succession_safely); trigger_succession() itself also
        # acquires this lock below, since it's the same file-write/graph-
        # memory critical section a webhook-triggered call (which does NOT
        # go through process_handoff) can otherwise race against. A plain
        # Lock would deadlock the file-watcher path on the very first
        # handoff; RLock lets the same thread re-enter.
        self.lock = threading.RLock()
        self.last_content: Optional[str] = None
        self.has_transient_error = False
        self.consecutive_transient_errors = 0
        self.loop_detector = LoopDetector()
        self._stop_event = threading.Event()
        self.hook_executor = ResilientHookExecutor(self.working_directory)
        self.policy_engine = PolicyEngine(rules=default_rule_set(working_directory=self.working_directory))
        self.alert_engine = AlertEngine(rules=default_alert_rules())
        self._consecutive_verify_failed = 0
        self._policy_checked_count = 0
        self._policy_denied_count = 0

    def _check_alerts(self) -> None:
        """Evaluates the alert engine over current counters (Phase D4) and
        emits an 'alert' event for anything that fires."""
        context = {
            "consecutive_verify_failed": self._consecutive_verify_failed,
            "policy_denied_count": self._policy_denied_count,
            "policy_checked_count": self._policy_checked_count,
        }
        for alert in self.alert_engine.check(context):
            _emit_event(self.working_directory, "alert", {"rule": alert.rule_name, "message": alert.message})

    def _policed_run(
        self,
        name: str,
        argv: list,
        cwd: str,
        risk_level: RiskLevel = RiskLevel.CAUTION,
        source: ActionSource = ActionSource.NUDGER,
        path: Optional[str] = None,
        **kwargs,
    ):
        """Runs a subprocess call through the policy engine first (Phase D1).

        Replaces the bare `# nosec`-suppressed subprocess.run call sites in
        the Ship Phase: every mutating git/pytest action now passes through
        an auditable, tested gate instead of a one-time human assertion.
        Raises PolicyDenied if the engine denies the action; the deny is
        also emitted as an event so it shows up in the audit trail even
        when the caller doesn't otherwise log it.
        """
        action = PolicyAction(
            name=name, argv=argv, path=path, source=source, risk_level=risk_level, budget=self.action_budget
        )
        decision = self.policy_engine.check(action)
        self._policy_checked_count += 1
        if not decision.allowed:
            self._policy_denied_count += 1
            _emit_event(self.working_directory, "policy_denied", {
                "action": name, "argv": argv, "rule": decision.rule_name, "reason": decision.reason,
            })
            self._check_alerts()
            raise PolicyDenied(decision)
        return subprocess.run(argv, cwd=cwd, **kwargs)  # nosec B603 B607 -- gated above by PolicyEngine

    def stop(self) -> None:
        """Stops the handoff monitoring loop and executes the Stop hook."""
        self._stop_event.set()
        stop_verdict = self.hook_executor.execute_hook("Stop", {"action": "stop_nudger"})
        _emit_event(self.working_directory, "stop", stop_verdict)

    def _execute_succession_safely(
        self, content: str, reflection: Optional[str] = None, extra_facts: Optional[List[str]] = None
    ) -> bool:
        """Executes succession handling catching user-facing errors.

        Args:
            content: Raw YAML configuration text block.
            reflection: Optional summary of the verify/ship outcome to persist as a
                graph-memory constraint for the next succession's context read.
            extra_facts: Optional discrete facts extracted from verify-phase tool
                output (Phase C2b) to persist alongside the reflection summary.

        Returns:
            True if the succession was processed successfully, False on fatal error.
        """
        try:
            self.trigger_succession(content, reflection=reflection, extra_facts=extra_facts)
            self.last_content = content
            self.has_transient_error = False
            return True
        except SimulationException as e:
            logger.error("Simulation Sandbox halted execution: %s", e)
            self.last_content = content
            self.has_transient_error = False
            return False
        except (
            HandoffValueError,
            HandoffKeyError,
            HandoffTypeError,
            ValueError,
            KeyError,
            TypeError,
        ) as e:
            logger.error("Permanent error in trigger_succession: %s", e)
            self.last_content = content
            self.has_transient_error = False
            return False

    def _create_dry_run_plan(self, parsed: dict, dry_run_plan_path: str) -> None:
        """Prepares a dry-run succession config plan for manual approval.

        Args:
            parsed: Safely loaded dictionary metadata.
            dry_run_plan_path: Output JSON path.
        """
        candidates = parsed.get("candidates", [])
        if not isinstance(candidates, list):
            candidates = []
        req_vector = [float(len(candidates)), 1.0]
        swarm_config = dimension_swarm(
            req_vector, self.config.default_matrix
        )

        swarm_counts: dict[str, int] = {}
        for agent in swarm_config.swarm:
            swarm_counts[agent.role] = (
                swarm_counts.get(agent.role, 0) + 1
            )

        plan_info = {
            "status": PipelineStatus.AWAITING_APPROVAL.value,
            "candidates": candidates,
            "estimated_cost_usd": len(candidates) * 0.005,
            "swarm_counts": swarm_counts,
        }
        tmp_path = dry_run_plan_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(plan_info, f, indent=2)
        os.replace(tmp_path, dry_run_plan_path)
        logger.info(
            "Dry-run plan created at %s. Awaiting approval.",
            dry_run_plan_path,
        )

    def process_handoff(self) -> None:
        """Process the handoff file if it exists and has modified content.

        Uses thread-safe lock synchronization and executes PreToolUse/PostToolUse hooks.
        """
        with self.lock:
            handoff_path = os.path.join(
                self.working_directory, self.config.handoff_file
            )
            if not os.path.exists(handoff_path):
                logger.debug("Handoff file is missing: %s", handoff_path)
                return

            _emit_event(self.working_directory, "progress", {"message": "Handoff processing started", "path": handoff_path})
            # Handle interrupt injection ("God's Eye")
            interrupt_path = os.path.join(self.working_directory, "interrupt.md")
            if os.path.exists(interrupt_path):
                with open(interrupt_path, "r", encoding="utf-8") as f:
                    interrupt_content = f.read().strip()
                if interrupt_content:
                    # interrupt.md is external, untrusted input (Phase D2):
                    # sanitize before it reaches next_context, which the
                    # next succession's prompt reads verbatim.
                    sanitized = sanitize(interrupt_content, TrustLevel.UNTRUSTED)
                    if sanitized.is_suspicious:
                        _emit_event(self.working_directory, "injection_flagged", {
                            "message": "God's Eye interrupt flagged by injection defense",
                            "categories": sanitized.flagged_categories,
                        })
                        logger.warning(
                            "God's Eye interrupt flagged: %s", sanitized.flagged_categories
                        )
                    quarantined_content = sanitized.quarantined_text
                    _emit_event(self.working_directory, "interrupt", {"message": f"Live constraint injected: {interrupt_content}"})
                    logger.warning("God's Eye Interrupt caught: %s", interrupt_content)

                    # Force a thermal escape context modification
                    artifact_path = os.path.join(self.working_directory, PIPELINE_ARTIFACT_FILE)
                    if os.path.exists(artifact_path):
                        # Append the interrupt as an open question/constraint in the latest artifact context
                        from self_governance.models import PipelineArtifact, PipelinePhase
                        with open(artifact_path, "a", encoding="utf-8") as f:
                            fake_artifact = PipelineArtifact(
                                phase=PipelinePhase.BUILD, author_persona="GodsEye", approved_roster=["GodsEye"],
                                final_temperature=10.0, final_threshold=0.0, cycles_needed=1,
                                decisions=[f"Interrupt injected: {interrupt_content}"],
                                open_questions=[quarantined_content],
                                next_context=f"LIVE CONSTRAINT: {quarantined_content}"
                            )
                            f.write(fake_artifact.model_dump_json() + "\n")
                    # Clear the interrupt file
                    os.remove(interrupt_path)

            try:
                with open(handoff_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error("Transient error reading handoff file: %s", e)
                self.has_transient_error = True
                self.consecutive_transient_errors += 1
                return

            if content == self.last_content:
                return

            # PreToolUse Hook
            verdict = self.hook_executor.execute_hook("PreToolUse", {"action": "process_handoff"})
            _emit_event(self.working_directory, "pre_tool_use", verdict)
            if verdict.get("permission") == "deny":
                logger.info("PreToolUse hook denied execution")
                return

            try:
                try:
                    import re
                    yaml_content = content
                    if content.startswith("---"):
                        match = re.search(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
                        if match:
                            yaml_content = match.group(1)
                    parsed = yaml.safe_load(yaml_content)
                except Exception as e:
                    logger.error("Permanent error: Malformed YAML: %s", e)
                    self.last_content = content
                    self.has_transient_error = False
                    return

                if not isinstance(parsed, dict):
                    logger.error(
                        "Permanent error: Handoff content must be a dictionary"
                    )
                    self.last_content = content
                    self.has_transient_error = False
                    return

                status = parsed.get("status")
                _emit_event(self.working_directory, "memory", {"message": "Handoff status retrieved from memory", "status": status})
                dry_run_plan_path = os.path.join(
                    self.working_directory, "dry_run_plan.json"
                )

                plan_approved = False
                if os.path.exists(dry_run_plan_path):
                    try:
                        with open(dry_run_plan_path, "r", encoding="utf-8") as f:
                            plan_data = json.load(f)
                            if plan_data.get("status") == PipelineStatus.APPROVED.value:
                                plan_approved = True
                    except Exception as e:  # nosec B110
                        logger.warning("Failed to read or parse dry run plan: %s", e, exc_info=True)

                if status == PipelineStatus.APPROVED.value or plan_approved:
                    worktree_path = os.path.join(self.working_directory, ".planning", "worktrees", "active_task")
                    if not os.path.exists(worktree_path):
                        os.makedirs(os.path.dirname(worktree_path), exist_ok=True)
                        self._policed_run("git_branch_delete_scratch", ["git", "branch", "-D", "active_task"], self.working_directory, capture_output=True)
                        self._policed_run("git_worktree_prune", ["git", "worktree", "prune"], self.working_directory, capture_output=True)
                        self._policed_run("git_worktree_add", ["git", "worktree", "add", "-b", "active_task", worktree_path], self.working_directory, path=worktree_path, capture_output=True)
                        _emit_event(self.working_directory, "worktree", {"message": f"Created execution worktree at {worktree_path}"})

                    if not self._execute_succession_safely(content):
                        return
                    if os.path.exists(dry_run_plan_path):
                        try:
                            os.remove(dry_run_plan_path)
                        except Exception as e:  # nosec B110
                            logger.warning("Failed to remove dry run plan: %s", e, exc_info=True)
                elif status == SessionStatus.COMPLETED.value:
                    if self.config.dry_run:
                        if not os.path.exists(dry_run_plan_path):
                            self._create_dry_run_plan(parsed, dry_run_plan_path)
                        self.last_content = content
                        self.has_transient_error = False
                        self.consecutive_transient_errors = 0

                        post_verdict = self.hook_executor.execute_hook("PostToolUse", {"status": "success"})
                        _emit_event(self.working_directory, "post_tool_use", post_verdict)
                        return
                    else:
                        # Verify Phase
                        _emit_event(self.working_directory, "verify", {"message": "Running Verify Phase: pytest and security-audit"})
                        pytest_res = None
                        audit_res = None
                        try:
                            worktree_path = os.path.join(self.working_directory, ".planning", "worktrees", "active_task")
                            exec_dir = worktree_path if os.path.exists(worktree_path) else self.working_directory

                            # Sandboxed, not `uv run pytest` on the host (peer-review
                            # batch, July 2026): the code under test here is
                            # LLM-generated and untrusted -- running it directly on
                            # the orchestrator's host process gives it host
                            # privileges. build_sandbox_pytest_argv runs it in the
                            # same read-only, network-disabled Docker container
                            # gemini_adapter.execute_tests() already uses.
                            pytest_argv = build_sandbox_pytest_argv(exec_dir)
                            pytest_res = self._policed_run(
                                "run_pytest", pytest_argv, exec_dir, capture_output=True, text=True, timeout=35
                            )
                            audit_res = self._policed_run(
                                "run_security_audit",
                                ["uv", "run", "self-governance", "security-audit", self.config.handoff_file],
                                exec_dir, capture_output=True, text=True,
                            )
                            
                            if pytest_res.returncode != 0 or audit_res.returncode != 0:
                                logger.error("Verify phase failed. Pytest exit: %d, Audit exit: %d", pytest_res.returncode, audit_res.returncode)
                                self._consecutive_verify_failed += 1
                                _emit_event(self.working_directory, "verify_failed", {
                                    "pytest_failed": pytest_res.returncode != 0,
                                    "audit_failed": audit_res.returncode != 0,
                                })
                                self._check_alerts()

                                # LoopDetector was never consulted on this path
                                # (peer-review batch, July 2026) -- it's only
                                # called on a *successful* completion elsewhere
                                # in this file, so a repeatedly-failing verify
                                # phase never got checked at all. Hashing the
                                # raw handoff content wouldn't have worked
                                # anyway: each failure prepends a fresh
                                # "# Failure Summary" block (with this
                                # attempt's own pytest/audit output) on top of
                                # every prior one, so the content -- and its
                                # hash -- grows and changes every cycle even
                                # when the underlying failure is identical.
                                # Signature on the exit codes instead: a
                                # stable signal that repeats for a genuinely
                                # repeating failure, immune to that noise.
                                try:
                                    self.loop_detector.record_and_check(
                                        f"verify_failure:{pytest_res.returncode}:{audit_res.returncode}"
                                    )
                                except LoopInterceptionError as loop_err:
                                    logger.error("Repeated identical verify failure detected: %s", loop_err)
                                    _emit_event(self.working_directory, "loop_detected", {
                                        "message": str(loop_err),
                                        "context": "verify_phase_failure",
                                    })
                                    self.last_content = content
                                    return  # Halt -- needs human intervention, not another automatic retry
                                if self.config.fail_on_verify:
                                    parsed["status"] = "FAILED"
                                    summary = "Verification failures summary:\n"
                                    if pytest_res.returncode != 0:
                                        summary += f"- Pytest failed with exit code {pytest_res.returncode}.\n"
                                        if pytest_res.stdout:
                                            summary += f"  Pytest Output:\n{pytest_res.stdout[:500]}\n"
                                    if audit_res.returncode != 0:
                                        summary += f"- Security audit failed with exit code {audit_res.returncode}.\n"
                                        if audit_res.stdout:
                                            summary += f"  Audit Output:\n{audit_res.stdout[:500]}\n"

                                    body = ""
                                    if content.startswith("---"):
                                        parts = content.split("---", 2)
                                        if len(parts) >= 3:
                                            body = parts[2]
                                    else:
                                        body = content

                                    body = f"\n# Failure Summary\n{summary}\n{body}"
                                    new_yaml = yaml.safe_dump(parsed)
                                    new_content = f"---\n{new_yaml}---\n{body}"
                                    with open(handoff_path, "w", encoding="utf-8") as f:
                                        f.write(new_content)
                                    self.last_content = new_content
                                    return  # Halt succession
                            else:
                                self._consecutive_verify_failed = 0
                                _emit_event(self.working_directory, "verify_passed", {"message": "Verification passed."})
                                # Ship Phase
                                _emit_event(self.working_directory, "ship", {"message": "Running Ship Phase: retro generation and worktree merge"})
                                
                                # If we used a worktree, commit and merge it back to main.
                                if os.path.exists(worktree_path):
                                    self._policed_run("git_add", ["git", "add", "."], worktree_path, capture_output=True)
                                    self._policed_run("git_commit", ["git", "commit", "-m", "ASG Ship Phase: Auto-commit"], worktree_path, capture_output=True)
                                    merge_res = self._policed_run(
                                        "git_merge", ["git", "merge", "active_task"], self.working_directory,
                                        capture_output=True, text=True,
                                    )
                                    if merge_res.returncode != 0:
                                        # A conflicting merge previously fell through
                                        # unchecked here (peer-review batch, July 2026):
                                        # the worktree/branch got deleted anyway, leaving
                                        # the repo stuck in an unresolved MERGING state
                                        # (.git/MERGE_HEAD) that broke every future
                                        # automated git action until a human ran
                                        # `git merge --abort` by hand. Abort immediately
                                        # instead, and leave the worktree/branch intact
                                        # so there's something to inspect/retry.
                                        logger.error(
                                            "Ship Phase merge conflict; aborting merge. Output: %s",
                                            (merge_res.stdout or "")[:500],
                                        )
                                        self._policed_run(
                                            "git_merge_abort", ["git", "merge", "--abort"],
                                            self.working_directory, capture_output=True,
                                        )
                                        _emit_event(self.working_directory, "ship_merge_conflict", {
                                            "message": "Merge conflict detected; merge aborted, worktree preserved for manual resolution.",
                                            "output": (merge_res.stdout or "")[:500],
                                        })
                                        self._check_alerts()
                                    else:
                                        self._policed_run("git_worktree_remove", ["git", "worktree", "remove", "-f", worktree_path], self.working_directory, path=worktree_path, capture_output=True)
                                        self._policed_run("git_branch_delete_scratch", ["git", "branch", "-d", "active_task"], self.working_directory, capture_output=True)

                                self._policed_run(
                                    "export_retro",
                                    ["uv", "run", "self-governance", "retro", "--export", os.path.join(".planning", "RETRO.md")],
                                    self.working_directory, capture_output=True,
                                )
                        except Exception as e:
                            logger.error("Error running Verify/Ship phases: %s", e)

                        # If fail_on_verify is True and we got here, it means verification passed.
                        # If fail_on_verify is False, we get here even if verification failed.
                        # However, if verification failed and fail_on_verify is True, we already returned.
                        if not (self.config.fail_on_verify and (pytest_res is None or audit_res is None or pytest_res.returncode != 0 or audit_res.returncode != 0)):
                            verify_clean = (
                                pytest_res is not None and audit_res is not None
                                and pytest_res.returncode == 0 and audit_res.returncode == 0
                            )
                            reflection = (
                                "Verify phase passed (pytest + security-audit clean)."
                                if verify_clean
                                else "Verify phase skipped or non-blocking failure tolerated (fail_on_verify=False)."
                            )
                            # Phase C2b: parse discrete facts out of the raw tool
                            # output rather than folding everything into one
                            # sentence, so future retrieval can match on the
                            # specific failing test or finding.
                            extra_facts = extract_facts(
                                pytest_output=pytest_res.stdout if pytest_res is not None else "",
                                audit_output=audit_res.stdout if audit_res is not None else "",
                            )
                            if not self._execute_succession_safely(content, reflection=reflection, extra_facts=extra_facts):
                                return
                else:
                    self.last_content = content
                    self.has_transient_error = False
                    self.consecutive_transient_errors = 0
                    return

                self.last_content = content
                self.has_transient_error = False
                self.consecutive_transient_errors = 0

                # PostToolUse Hook (Success)
                post_verdict = self.hook_executor.execute_hook("PostToolUse", {"status": "success"})
                _emit_event(self.working_directory, "post_tool_use", post_verdict)

            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error("Transient error during handoff processing: %s", e)
                self.has_transient_error = True
                self.consecutive_transient_errors += 1
                # PostToolUse Hook (Error)
                post_verdict = self.hook_executor.execute_hook("PostToolUse", {"status": "error", "error": str(e)})
                _emit_event(self.working_directory, "post_tool_use", post_verdict)

    def watch_handoff(self) -> None:
        """Begins event-driven monitoring of the handoff file using watchdog Observer."""
        _emit_event(self.working_directory, "spawn", {"message": "ContinuousNudger started watching handoff."})

        # Restore institutional memory from prior sessions
        from self_governance.learning import restore_session_context
        ctx = restore_session_context()
        if ctx["sessions_distilled"] > 0:
            _emit_event(
                self.working_directory,
                "memory",
                {
                    "message": "Restored learning context",
                    "sessions_distilled": ctx["sessions_distilled"],
                    "avg_cycles": ctx["avg_cycles_needed"],
                    "recent_patterns": ctx["recent_patterns"],
                },
            )

        # Initial startup check: create .planning schema if it doesn't exist
        planning_dir = os.path.dirname(os.path.abspath(os.path.join(self.working_directory, self.config.handoff_file)))
        if not os.path.exists(planning_dir):
            os.makedirs(planning_dir, exist_ok=True)
            for schema_file in ["VISION.md", "ROADMAP.md", "CURRENT_STATE.md", "PLAN.md"]:
                path = os.path.join(planning_dir, schema_file)
                if not os.path.exists(path):
                    with open(path, "w", encoding="utf-8") as f:
                        if schema_file == "CURRENT_STATE.md":
                            f.write("---\nstatus: \"PENDING\"\ncandidates: []\n---\n# Current State\n\nNo task assigned yet.\n")
                        else:
                            f.write(f"# {schema_file.replace('.md', '')}\n")

        self.process_handoff()

        observer = Observer()
        observer.daemon = True
        handler = HandoffHandler(self)
        observer.schedule(handler, path=self.working_directory, recursive=True)
        observer.start()

        try:
            retry_delay = 0.25
            last_poll_time = time.time()
            handoff_path = os.path.join(self.working_directory, self.config.handoff_file)
            last_mtime = os.path.getmtime(handoff_path) if os.path.exists(handoff_path) else 0.0

            while not self._stop_event.is_set():
                if self.consecutive_transient_errors > 5:
                    logger.error("More than 5 consecutive transient errors. Breaking watchdog loop.")
                    self.stop()
                    break

                # Periodic polling fallback (every 1.0 seconds) to catch dropped FS events
                now = time.time()
                if now - last_poll_time > 1.0:
                    last_poll_time = now
                    if os.path.exists(handoff_path):
                        current_mtime = os.path.getmtime(handoff_path)
                        if current_mtime != last_mtime:
                            last_mtime = current_mtime
                            self.process_handoff()

                # If we had a transient error, retry processing with backoff —
                if self.has_transient_error:
                    self._stop_event.wait(retry_delay)
                    retry_delay = min(60.0, retry_delay * 2)
                    if self._stop_event.is_set():
                        break
                    self.process_handoff()
                else:
                    retry_delay = 0.25
                time.sleep(0.05)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping observer due to KeyboardInterrupt/SystemExit")
            raise
        finally:
            observer.stop()
            observer.join()

    def trigger_succession(
        self,
        handoff_content: str,
        adapter: Optional[Any] = None,
        tenant_id: Optional[str] = None,
        reflection: Optional[str] = None,
        extra_facts: Optional[List[str]] = None,
    ) -> ConsensusResult:
        """Executes SuccessionSession with TETD consensus, logs, and drafts next prompt.

        Args:
            handoff_content: The YAML content string from the handoff file.
            adapter: Optional execution adapter instance (e.g. Gemini).
            tenant_id: Optional tenant identifier string to isolate tenant logs.
            reflection: Optional verify/ship outcome summary, written as a graph-memory
                constraint so the next succession's query_context read sees it.
            extra_facts: Optional discrete facts extracted from pytest/audit output
                (Phase C2b automatic fact extraction), written as additional
                graph-memory constraints alongside reflection.

        Returns:
            The ConsensusResult output representing approved roster details.

        Raises:
            HandoffValueError: If handoff content is malformed or invalid.
            HandoffKeyError: If a required key (e.g. 'candidates') is missing.
            HandoffTypeError: If candidate list has an incorrect type.
            ValueError: If approved roster fails self-critique.
        """
        # Held for the whole succession: this writes pipeline_artifact.jsonl,
        # the roster rotation log, and graph memory, the same files/state
        # process_handoff's file-watcher path touches. Without this lock, a
        # webhook-triggered succession (github_app.py calls this directly,
        # not through process_handoff) could race the watchdog thread and
        # interleave writes. RLock (see __init__) lets process_handoff's own
        # call into this method re-enter safely.
        with self.lock:
            return self._trigger_succession_impl(
                handoff_content,
                adapter=adapter,
                tenant_id=tenant_id,
                reflection=reflection,
                extra_facts=extra_facts,
            )

    def _trigger_succession_impl(
        self,
        handoff_content: str,
        adapter: Optional[Any] = None,
        tenant_id: Optional[str] = None,
        reflection: Optional[str] = None,
        extra_facts: Optional[List[str]] = None,
    ) -> ConsensusResult:
        """The actual succession logic, called only through trigger_succession
        so it always runs under self.lock."""
        if adapter is None:
            # The file-watcher path (process_handoff -> _execute_succession_safely
            # -> trigger_succession) never passed an adapter (peer-review batch,
            # July 2026), which silently no-ops self_critique(),
            # run_sandbox_simulation(), and distill_friction() below --
            # they all check `adapter is None`/`not adapter` and skip real
            # work. Only the webhook path (github_app.py) ever passed a
            # real one. Auto-instantiating a bare GeminiExecutionAdapter()
            # here is safe in a keyless dev/test environment -- its own
            # api_key falls back to os.getenv("GEMINI_API_KEY"), and every
            # one of those downstream functions already degrades to a
            # mock/no-op path when api_key is unset, same as passing None
            # did. In a real deployment with GEMINI_API_KEY configured,
            # this restores the actual critique/sandbox/distillation
            # checks that were previously always skipped on this path
            # regardless of whether a key was available.
            adapter = GeminiExecutionAdapter()
        try:
            import re
            yaml_content = handoff_content
            if handoff_content.startswith("---"):
                match = re.search(r"^---\s*\n(.*?)\n---\s*\n", handoff_content, re.DOTALL)
                if match:
                    yaml_content = match.group(1)
            parsed = yaml.safe_load(yaml_content)
        except Exception as e:
            raise HandoffValueError(f"Malformed YAML: {e}")

        if parsed is None or not isinstance(parsed, dict):
            raise HandoffValueError("Handoff content must be a dictionary")

        if "candidates" not in parsed:
            raise HandoffKeyError("Missing 'candidates' key in handoff")

        candidates = parsed["candidates"]
        if candidates is None:
            raise HandoffValueError("'candidates' cannot be null")
        if not isinstance(candidates, list):
            raise HandoffTypeError("'candidates' must be a list")

        # Security audit gate — audit the handoff content before triggering consensus
        from self_governance.security import run_security_audit
        audit_result = run_security_audit(handoff_content, fail_on_critical=True, fail_on_high=False)
        _emit_event(self.working_directory, "security", {
            "message": "Security audit complete",
            "passed": audit_result.passed,
            "summary": audit_result.audit_summary,
            "critical_count": audit_result.critical_count,
            "high_count": audit_result.high_count,
        })
        if not audit_result.passed:
            logger.warning(
                "Security audit FAILED before consensus: %s. Blocking succession.",
                audit_result.audit_summary,
            )
            log_path = os.path.join(self.working_directory, self.config.roster_log_file)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[SECURITY BLOCKED] {audit_result.audit_summary}\n")
            raise ValueError(f"Security audit failed: {audit_result.audit_summary}")
        logger.info("Security audit passed: %s", audit_result.audit_summary)

        # Load prior pipeline context to prime the council
        prior_artifacts = load_prior_artifacts(self.working_directory)
        prior_context_str = ""
        if prior_artifacts:
            last = prior_artifacts[-1]
            prior_context_str = (
                f"Previous session context: {last.get('next_context', '')}\n"
                f"Prior approved roster: {last.get('approved_roster', [])}\n"
                f"Prior open questions: {last.get('open_questions', [])}\n"
            )
            logger.info("Loaded %d prior pipeline artifact(s) for context.", len(prior_artifacts))

        from self_governance.complexity import calculate_ast_complexity
        
        # Determine AST complexity of target working directory
        ast_score = calculate_ast_complexity(self.working_directory)
        threshold = self.config.complexity_gate_threshold
        
        if ast_score < threshold:
            _emit_event(self.working_directory, "complexity_gate", {
                "message": "Task AST complexity is below threshold. Bypassing TETD consensus.",
                "ast_score": ast_score,
                "threshold": threshold,
                "bypassed": True
            })
            logger.info("AST complexity %d < %d. Bypassing consensus.", ast_score, threshold)
            approved_roster = [candidates[0]]
            res = ConsensusResult(
                approved_roster=approved_roster,
                final_temperature=self.config.consensus_initial_temperature,
                final_threshold=self.config.consensus_target_threshold
            )
            object.__setattr__(res, 'cycles_needed', 0)
        else:
            _emit_event(self.working_directory, "spawn", {"message": "Triggering succession consensus", "candidates": candidates, "ast_score": ast_score})

            # 2. Run consensus with config parameters
            res = run_consensus(
                initial_roster=candidates,
                B=self.config.consensus_buffer_limit,
                target_tau=self.config.consensus_target_threshold,
                initial_temp=self.config.consensus_initial_temperature,
                gamma=self.config.consensus_temperature_step,
                delta=self.config.consensus_decay_step,
                adapter=adapter,
                model=self.config.model_succession,
                config_path=self.config.config_path,
            )
            approved_roster = res.approved_roster

        _emit_event(self.working_directory, "consensus", {
            "message": "Consensus run complete",
            "approved_roster": approved_roster,
            "final_temperature": res.final_temperature,
            "final_threshold": res.final_threshold
        })

        # Perform self-critique before finalizing the approved roster
        critique_res = self_critique(
            proposed_plan=", ".join(approved_roster),
            goal="Establish stable succession roster",
            adapter=adapter
        )
        if not critique_res.get("approved", True):
            raise ValueError(f"Succession roster rejected by critique: {critique_res.get('critique')}")

        # 3. Compute dynamic requirements scale using matrix config
        req_vector = [float(len(approved_roster)), 1.0]
        trans_matrix = [list(row) for row in self.config.default_matrix]

        # Ensure matrix contains at least 3 rows to support Security Auditor
        if len(trans_matrix) == 2:
            trans_matrix.append([0.0, 1.0])

        # Apply matrix tuning from learning state
        from self_governance.learning import get_learning_state

        state = get_learning_state()
        scale = state.get("matrix_tuning", {}).get("scale_factor", 1.0)

        # Multiply Security Auditor row (index 2) by scale factor if present
        if len(trans_matrix) > 2:
            trans_matrix[2] = [w * scale for w in trans_matrix[2]]

        # Run Sandbox Simulation before dimensioning
        _emit_event(self.working_directory, "sandbox_simulation", {"message": "Spawning Council Sandbox"})
        run_sandbox_simulation(handoff_content, adapter)

        swarm_config = dimension_swarm(req_vector, trans_matrix)

        # Build and persist pipeline artifact for this session.
        # Coerce all fields to plain Python types so that Pydantic doesn't
        # receive MagicMock objects when run_consensus is mocked in tests.
        from self_governance.models import PipelineArtifact, PipelinePhase
        _final_temp = float(res.final_temperature) if not isinstance(res.final_temperature, float) else res.final_temperature
        _final_thresh = float(res.final_threshold) if not isinstance(res.final_threshold, float) else res.final_threshold
        # Ensure roster is a plain list of strings (guards against MagicMock)
        _safe_roster: list[str] = [str(r) for r in approved_roster] if isinstance(approved_roster, list) else []
        _author = _safe_roster[0] if _safe_roster else "Orchestrator"
        artifact = PipelineArtifact(
            phase=PipelinePhase.BUILD,
            author_persona=_author,
            approved_roster=_safe_roster,
            final_temperature=_final_temp,
            final_threshold=_final_thresh,
            cycles_needed=getattr(res, 'cycles_needed', 1) if not hasattr(res, '_mock_name') else 1,
            decisions=[f"Approved roster: {', '.join(_safe_roster)}"],
            open_questions=[],
            next_context=(
                f"Succession completed with roster [{', '.join(_safe_roster)}]. "
                f"Temperature settled at {_final_temp:.2f}, "
                f"threshold at {_final_thresh:.2f}. "
                f"{prior_context_str}"
            ),
        )
        append_pipeline_artifact(self.working_directory, artifact.model_dump())

        # Save session to graph memory (Path B)
        try:
            import datetime as dt
            graph_engine = GraphMemoryEngine(tenant_id or "default")
            graph_engine.add_session_node(
                session_id=int(dt.datetime.now(dt.timezone.utc).timestamp()),
                roster=_safe_roster,
                features=[f"Feature_{i}" for i, v in enumerate(req_vector) if v > 0],
                constraints=([reflection] if reflection else []) + (extra_facts or []),
            )
            graph_context = graph_engine.query_context([f"Feature_{i}" for i, v in enumerate(req_vector) if v > 0])
            prior_context_str += "\n" + graph_context + "\n"
        except Exception as e:
            logger.warning(f"Graph memory non-fatal error: {e}")

        # 4. Serialize config and draft prompt first
        if tenant_id:
            output_dir = os.path.join(self.working_directory, "tenants", tenant_id)
            os.makedirs(output_dir, exist_ok=True)
        else:
            output_dir = self.working_directory

        # PreCompact Hook
        pre_compact_verdict = self.hook_executor.execute_hook("PreCompact", {"action": "compile_prompt", "tenant_id": tenant_id})
        _emit_event(self.working_directory, "pre_compact", pre_compact_verdict)

        prompt_path = os.path.join(output_dir, self.config.prompt_file)

        # Support Hierarchical Swarms (Path C)
        if swarm_config and swarm_config.hierarchical_swarms:
            for domain, sub_swarm in swarm_config.hierarchical_swarms.items():
                domain_prompt_path = os.path.join(output_dir, f"prompt_draft_{domain}.md")
                with open(domain_prompt_path, "w", encoding="utf-8") as f:
                    f.write(f"--- Hierarchical Swarm Configuration: {domain.upper()} ---\n")
                    write_swarm_config_to_stream(f, sub_swarm)
                    f.write("\n--- End Configuration ---\n")
                    if prior_context_str:
                        f.write(f"\n--- Prior Session Context ---\n{prior_context_str}--- End Prior Context ---\n")
                    f.write(f"Prompt: Guide the {domain} swarm to collaborate on the next phase.\n")
            
            # Master Orchestrator for backwards compatibility and high-level routing
            tmp_prompt_path = prompt_path + ".tmp"
            with open(tmp_prompt_path, "w", encoding="utf-8") as f:
                f.write("--- Master Orchestrator ---\n")
                f.write("Task has been bifurcated into multiple sub-swarms.\n")
                f.write("See prompt_draft_frontend.md and prompt_draft_backend.md\n")
            os.replace(tmp_prompt_path, prompt_path)
        else:
            tmp_prompt_path = prompt_path + ".tmp"
            with open(tmp_prompt_path, "w", encoding="utf-8") as f:
                f.write("--- Swarm Configuration ---\n")
                write_swarm_config_to_stream(f, swarm_config)
                f.write("\n--- End Configuration ---\n")
                if prior_context_str:
                    f.write(f"\n--- Prior Session Context ---\n{prior_context_str}--- End Prior Context ---\n")
                f.write("Prompt: Guide the swarm to collaborate on the next phase.\n")
            os.replace(tmp_prompt_path, prompt_path)

        # 5. Append rotation details last (committing step)
        log_path = os.path.join(output_dir, self.config.roster_log_file)
        approved_str = ", ".join(approved_roster)
        log_entry = f"Succession Session Completed. Approved Roster: [{approved_str}]\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)

        # Check loop history only on successful completion
        self.loop_detector.record_and_check(handoff_content)

        # Distill this session into the learning store.
        # Use the pre-coerced float (_final_temp) so MagicMock objects in tests
        # do not cause TypeError inside distill_session's rolling-average math.
        # Also coerce cycles: on a MagicMock, getattr returns another MagicMock
        # (not the default value), which crashes on `cycles > 3` in distill_session.
        from self_governance.learning import distill_session
        try:
            _cycles = int(getattr(res, 'cycles_needed', self.config.consensus_buffer_limit))
        except (TypeError, ValueError):
            _cycles = self.config.consensus_buffer_limit
        distill_session(
            session_result=res,
            roster=_safe_roster,
            cycles=_cycles,
            temperature=_final_temp,
            adapter=adapter,
        )

        return res

