import os
import time
import yaml
import json
import logging
import threading
from typing import Optional, Any
from self_governance.consensus import run_consensus, ConsensusResult
from self_governance.dimensioning import dimension_swarm
from self_governance.config import OrchestratorConfig
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("self_governance.nudger")


class HandoffValueError(ValueError):
    """Exception raised when handoff values are invalid or malformed."""

    pass


class HandoffKeyError(KeyError):
    """Exception raised when a required key is missing in handoff."""

    pass


class HandoffTypeError(TypeError):
    """Exception raised when handoff types are incorrect."""

    pass


def write_swarm_config_to_stream(stream, config) -> None:
    """
    Stream SwarmConfig serialization directly to the file handle block-by-block.
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
        # Convert agent (dataclass) to dict so it can be serialized to JSON
        agent_str = json.dumps(dict(agent), indent=2)
        indented_agent = "\n".join("    " + line for line in agent_str.splitlines())
        stream.write(indented_agent)
    stream.write("\n  ]\n")
    stream.write("}")


class HandoffHandler(FileSystemEventHandler):
    def __init__(self, nudger: "ContinuousNudger") -> None:
        self.nudger = nudger

    def on_modified(self, event):
        if (
            not event.is_directory
            and os.path.basename(event.src_path) == self.nudger.config.handoff_file
        ):
            self.nudger.process_handoff()

    def on_created(self, event):
        if (
            not event.is_directory
            and os.path.basename(event.src_path) == self.nudger.config.handoff_file
        ):
            self.nudger.process_handoff()


class ContinuousNudger:
    """
    An event-driven file watcher that monitors handoff.md for a COMPLETED status.
    When triggered, it initiates a succession session and schedules the next phase.
    """

    def __init__(
        self, working_directory: str, config: Optional[OrchestratorConfig] = None
    ) -> None:
        """
        Initialize ContinuousNudger.

        Args:
            working_directory: The directory where handoff.md, logs, and prompt drafts are located.
            config: Optional OrchestratorConfig instance.
        """
        self.working_directory = working_directory
        self.config = config if config is not None else OrchestratorConfig()
        self.lock = threading.Lock()
        self.last_content: Optional[str] = None
        self.has_transient_error = False
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Stop the handoff monitoring loop."""
        self._stop_event.set()

    def process_handoff(self) -> None:
        """
        Process the handoff file if it exists and has modified content.
        Uses thread-safe lock synchronization.
        """
        with self.lock:
            handoff_path = os.path.join(
                self.working_directory, self.config.handoff_file
            )
            if not os.path.exists(handoff_path):
                return

            try:
                with open(handoff_path, "r", encoding="utf-8") as f:
                    content = f.read()
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                logger.error("Transient error reading handoff file: %s", e)
                self.has_transient_error = True
                return

            if content == self.last_content:
                return

            try:
                # 1. Try parsing YAML
                try:
                    parsed = yaml.safe_load(content)
                except Exception as e:
                    logger.error("Permanent error: Malformed YAML: %s", e)
                    self.last_content = content
                    self.has_transient_error = False
                    return

                # 2. Check if parsed is valid dictionary
                if not isinstance(parsed, dict):
                    logger.error(
                        "Permanent error: Handoff content must be a dictionary"
                    )
                    self.last_content = content
                    self.has_transient_error = False
                    return

                # 3. Check status and dry_run configurations
                status = parsed.get("status")
                dry_run_plan_path = os.path.join(
                    self.working_directory, "dry_run_plan.json"
                )

                plan_approved = False
                if os.path.exists(dry_run_plan_path):
                    try:
                        with open(dry_run_plan_path, "r", encoding="utf-8") as f:
                            plan_data = json.load(f)
                            if plan_data.get("status") == "APPROVED":
                                plan_approved = True
                    except Exception:  # nosec B110
                        pass

                if status == "APPROVED" or plan_approved:
                    try:
                        self.trigger_succession(content)
                        if os.path.exists(dry_run_plan_path):
                            try:
                                os.remove(dry_run_plan_path)
                            except Exception:  # nosec B110
                                pass
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
                        return
                elif status == "COMPLETED":
                    if self.config.dry_run:
                        if not os.path.exists(dry_run_plan_path):
                            candidates = parsed.get("candidates", [])
                            if not isinstance(candidates, list):
                                candidates = []
                            req_vector = [float(len(candidates)), 1.0]
                            swarm_config = dimension_swarm(
                                req_vector, self.config.default_matrix
                            )

                            swarm_counts = {}
                            for agent in swarm_config.swarm:
                                swarm_counts[agent.role] = (
                                    swarm_counts.get(agent.role, 0) + 1
                                )

                            plan_info = {
                                "status": "AWAITING_APPROVAL",
                                "candidates": candidates,
                                "estimated_cost_usd": len(candidates) * 0.005,
                                "swarm_counts": swarm_counts,
                            }
                            with open(dry_run_plan_path, "w", encoding="utf-8") as f:
                                json.dump(plan_info, f, indent=2)
                            logger.info(
                                "Dry-run plan created at %s. Awaiting approval.",
                                dry_run_plan_path,
                            )

                        self.last_content = content
                        self.has_transient_error = False
                        return
                    else:
                        try:
                            self.trigger_succession(content)
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
                            return
                else:
                    self.last_content = content
                    self.has_transient_error = False
                    return

                self.last_content = content
                self.has_transient_error = False

            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as e:
                # Transient error (like write errors in trigger_succession)
                logger.error("Transient error during handoff processing: %s", e)
                self.has_transient_error = True

    def watch_handoff(self) -> None:
        """
        Begins event-driven monitoring of handoff.md.
        """
        # Initial startup check: process handoff.md if it already exists
        self.process_handoff()

        observer = Observer()
        observer.daemon = True
        handler = HandoffHandler(self)
        observer.schedule(handler, path=self.working_directory, recursive=False)
        observer.start()

        try:
            while not self._stop_event.is_set():
                # If we had a transient error, retry processing
                if self.has_transient_error:
                    self.process_handoff()
                time.sleep(0.05)
        except (KeyboardInterrupt, SystemExit):
            logger.info("Stopping observer due to KeyboardInterrupt/SystemExit")
            raise
        finally:
            observer.stop()
            observer.join()

    def trigger_succession(
        self, handoff_content: str, adapter: Optional[Any] = None
    ) -> ConsensusResult:
        """
        Execute SuccessionSession with TETD consensus, append logs, and draft next prompt.

        Args:
            handoff_content: The YAML content from the handoff file.
            adapter: Optional GeminiExecutionAdapter instance.

        Raises:
            HandoffValueError: If handoff content is malformed or invalid.
            HandoffKeyError: If a required key (e.g. 'candidates') is missing.
            HandoffTypeError: If candidate list has an incorrect type.
        """
        try:
            parsed = yaml.safe_load(handoff_content)
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

        # 2. Run consensus with config parameters
        res = run_consensus(
            initial_roster=candidates,
            B=self.config.consensus_buffer_limit,
            target_tau=self.config.consensus_target_threshold,
            initial_temp=self.config.consensus_initial_temperature,
            gamma=self.config.consensus_temperature_step,
            delta=self.config.consensus_decay_step,
            adapter=adapter,
        )
        approved_roster = res.approved_roster

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

        swarm_config = dimension_swarm(req_vector, trans_matrix)

        # 4. Serialize config and draft prompt first
        prompt_path = os.path.join(self.working_directory, self.config.prompt_file)
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write("--- Swarm Configuration ---\n")
            write_swarm_config_to_stream(f, swarm_config)
            f.write("\n--- End Configuration ---\n")
            f.write("Prompt: Guide the swarm to collaborate on the next phase.\n")

        # 5. Append rotation details last (committing step)
        log_path = os.path.join(self.working_directory, self.config.roster_log_file)
        approved_str = ", ".join(approved_roster)
        log_entry = f"Succession Session Completed. Approved Roster: [{approved_str}]\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)

        return res
