"""Configuration management module for the Self-Governance orchestrator.

Loads and validates configurations from YAML files or merges with default options
for consensus parameters, watcher files, dimensioning matrices, model routing,
and advisor nudge limits.
"""

import os
import logging
import yaml
import copy
from typing import Dict, Any, List, Optional

logger = logging.getLogger("self_governance.config")

# Single source of truth for the fallback model name, used everywhere a
# model isn't otherwise configured. Override via config.yaml's `models:`
# section (preferred, per-purpose) or ASG_DEFAULT_MODEL (global override,
# e.g. for a non-Gemini provider) -- never hardcode a model name elsewhere.
DEFAULT_MODEL = os.getenv("ASG_DEFAULT_MODEL", "gemini-2.5-flash")

DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "consensus": {
        "buffer_limit": 3,
        "target_threshold": 9.0,
        "initial_temperature": 1.0,
        "temperature_step": 0.1,
        "decay_step": 0.5,
        "complexity_gate_threshold": 500,
    },
    "watcher": {
        "handoff_file": ".planning/CURRENT_STATE.md",
        "prompt_file": "prompt_draft.md",
        "roster_log_file": "roster_rotation_log.md",
        "dry_run": False,
        "fail_on_verify": True,
    },
    "dimensioning": {
        "default_matrix": [[1.0, 0.5], [0.0, 1.0]],
        "webhook_matrix": [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]],
    },
    "models": {
        "default": DEFAULT_MODEL,
        "succession": DEFAULT_MODEL,
        "development": DEFAULT_MODEL,
        "review": DEFAULT_MODEL,
        "security": DEFAULT_MODEL,
    },
    "advisor": {
        "enabled": True,
        "nudge_turn": 2,
        "nudge_text": "Please call advisor() before committing to an approach or declaring completion.",
        "max_tokens": 2048,
    },
    "project": {
        "persona_registry": "src/self_governance/assets/agents.json",
    },
}


class OrchestratorConfig:
    """Configuration manager for the Self-Governance orchestrator.

    Exposes typed properties and ensures user configuration files conform to defaults.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        """Initializes OrchestratorConfig by loading defaults and merging paths.

        Args:
            config_path: Optional path to a YAML configuration file.

        Raises:
            ValueError: If the user configuration file is invalid or types mismatch.
        """
        self.config_path = config_path
        self.config_data: Dict[str, Dict[str, Any]] = copy.deepcopy(DEFAULT_CONFIG)
        if config_path:
            if not os.path.exists(config_path):
                raise FileNotFoundError(f"Config file not found: {config_path}")
            # Fail fast: a config file the operator pointed at must load and
            # validate, never silently degrade to defaults.
            with open(config_path, "r", encoding="utf-8") as f:
                user_data = yaml.safe_load(f)
            if user_data is not None and not isinstance(user_data, dict):
                raise ValueError(f"Config file {config_path} must be a YAML mapping")
            if user_data:
                self._validate(user_data, config_path)
                self._merge_config(self.config_data, user_data)
            logger.info("Loaded configuration from %s", config_path)

    def _validate(self, user_data: Dict[str, Any], config_path: str) -> None:
        """Reject unknown keys and values whose type disagrees with the default.

        Args:
            user_data: The loaded YAML dictionary.
            config_path: Path of the file for error reporting.

        Raises:
            ValueError: If unknown keys are found or type checking fails.
        """
        unknown = set(user_data) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(
                f"Unknown config keys in {config_path}: {sorted(unknown)}"
            )
        for section, values in user_data.items():
            defaults = DEFAULT_CONFIG[section]
            if not isinstance(values, dict):
                raise ValueError(f"Config section '{section}' must be a mapping")
            for key, value in values.items():
                default = defaults.get(key)
                if default is None:
                    continue  # new keys within known sections are allowed
                expected = (int, float) if isinstance(default, (int, float)) else type(default)
                if isinstance(default, bool):
                    expected = bool
                if not isinstance(value, expected):
                    raise ValueError(
                        f"Config value {section}.{key} must be {expected}, "
                        f"got {type(value).__name__}: {value!r}"
                    )

    def _merge_config(self, base: Dict[str, Any], update: Dict[str, Any]) -> None:
        """Recursively merges user configurations into the base default configuration.

        Args:
            base: The base dictionary to merge into.
            update: The dictionary containing updating configuration mapping.
        """
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._merge_config(base[k], v)
            else:
                base[k] = v

    @property
    def consensus_buffer_limit(self) -> int:
        """Gets the consensus buffer iteration limit before TETD annealing starts."""
        return self.config_data["consensus"]["buffer_limit"]

    @property
    def consensus_target_threshold(self) -> float:
        """Gets the target score required for roster approval consensus."""
        return self.config_data["consensus"]["target_threshold"]

    @property
    def consensus_initial_temperature(self) -> float:
        """Gets the starting temperature for simulated annealing under deadlock."""
        return self.config_data["consensus"]["initial_temperature"]

    @property
    def consensus_temperature_step(self) -> float:
        """Gets the temperature scaling factor added per retry turn."""
        return self.config_data["consensus"]["temperature_step"]

    @property
    def consensus_decay_step(self) -> float:
        """Gets the target score threshold decay rate per retry turn."""
        return self.config_data["consensus"]["decay_step"]

    @property
    def complexity_gate_threshold(self) -> int:
        """Gets the AST complexity threshold for triggering TETD consensus."""
        return self.config_data["consensus"].get("complexity_gate_threshold", 500)

    @property
    def handoff_file(self) -> str:
        """Gets the handoff filename to watch."""
        return self.config_data["watcher"]["handoff_file"]

    @property
    def prompt_file(self) -> str:
        """Gets the filename where generated rosters are written."""
        return self.config_data["watcher"]["prompt_file"]

    @property
    def roster_log_file(self) -> str:
        """Gets the rotation log filename."""
        return self.config_data["watcher"]["roster_log_file"]

    @property
    def dry_run(self) -> bool:
        """Returns True if the orchestrator should run in dry-run mode."""
        return self.config_data["watcher"].get("dry_run", False)

    @property
    def fail_on_verify(self) -> bool:
        """Returns True if verification failures should halt the succession pipeline."""
        return self.config_data["watcher"].get("fail_on_verify", True)

    @property
    def default_matrix(self) -> List[List[float]]:
        """Gets the default SDLC subagent routing transition matrix."""
        return self.config_data["dimensioning"]["default_matrix"]

    @property
    def webhook_matrix(self) -> List[List[float]]:
        """Gets the transition matrix utilized for webhook-triggered pipelines."""
        return self.config_data["dimensioning"].get(
            "webhook_matrix", [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]]
        )

    @property
    def model_default(self) -> str:
        """Gets the fallback default LLM name."""
        return self.config_data["models"].get("default", DEFAULT_MODEL)

    @property
    def model_succession(self) -> str:
        """Gets the LLM routing target for consensus succession voting."""
        return self.config_data["models"].get("succession", self.model_default)

    @property
    def model_development(self) -> str:
        """Gets the LLM routing target for writing code."""
        return self.config_data["models"].get("development", self.model_default)

    @property
    def model_review(self) -> str:
        """Gets the LLM routing target for code review."""
        return self.config_data["models"].get("review", self.model_default)

    @property
    def model_security(self) -> str:
        """Gets the LLM routing target for vulnerability checks."""
        return self.config_data["models"].get("security", self.model_default)

    @property
    def advisor_enabled(self) -> bool:
        """Returns True if the high-intelligence advisor model is enabled."""
        return self.config_data["advisor"].get("enabled", True)

    @property
    def advisor_nudge_turn(self) -> int:
        """Gets the turn count at which to nudge the developer for advice."""
        return self.config_data["advisor"].get("nudge_turn", 2)

    @property
    def advisor_nudge_text(self) -> str:
        """Gets the nudge prompt instructions for advisor consultation."""
        return self.config_data["advisor"].get("nudge_text", "")

    @property
    def advisor_max_tokens(self) -> int:
        """Gets the max token output limit for the advisor call."""
        return self.config_data["advisor"].get("max_tokens", 2048)

    @property
    def project_persona_registry(self) -> str:
        """Gets the path to the project's persona registry JSON file."""
        return self.config_data["project"].get("persona_registry", "src/self_governance/assets/agents.json")

