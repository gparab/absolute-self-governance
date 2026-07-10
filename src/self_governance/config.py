import os
import logging
import yaml
import copy
from typing import Dict, Any, List, Optional

logger = logging.getLogger("self_governance.config")

DEFAULT_CONFIG: Dict[str, Dict[str, Any]] = {
    "consensus": {
        "buffer_limit": 3,
        "target_threshold": 9.0,
        "initial_temperature": 1.0,
        "temperature_step": 0.1,
        "decay_step": 0.5,
    },
    "watcher": {
        "handoff_file": "handoff.md",
        "prompt_file": "prompt_draft.md",
        "roster_log_file": "roster_rotation_log.md",
        "dry_run": False,
    },
    "dimensioning": {
        "default_matrix": [[1.0, 0.5], [0.0, 1.0]],
        "webhook_matrix": [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]],
    },
    "models": {
        "default": "gemini-2.5-flash",
        "succession": "gemini-2.5-flash",
        "development": "gemini-2.5-flash",
        "review": "gemini-2.5-flash",
        "security": "gemini-2.5-flash",
    },
    "advisor": {
        "enabled": True,
        "nudge_turn": 2,
        "nudge_text": "Please call advisor() before committing to an approach or declaring completion.",
        "max_tokens": 2048,
    },
}


class OrchestratorConfig:
    """
    Configuration manager for the Self-Governance orchestrator.
    """

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_data: Dict[str, Dict[str, Any]] = copy.deepcopy(DEFAULT_CONFIG)
        if config_path and os.path.exists(config_path):
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
        """Reject unknown keys and values whose type disagrees with the default."""
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
        for k, v in update.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._merge_config(base[k], v)
            else:
                base[k] = v

    @property
    def consensus_buffer_limit(self) -> int:
        return self.config_data["consensus"]["buffer_limit"]

    @property
    def consensus_target_threshold(self) -> float:
        return self.config_data["consensus"]["target_threshold"]

    @property
    def consensus_initial_temperature(self) -> float:
        return self.config_data["consensus"]["initial_temperature"]

    @property
    def consensus_temperature_step(self) -> float:
        return self.config_data["consensus"]["temperature_step"]

    @property
    def consensus_decay_step(self) -> float:
        return self.config_data["consensus"]["decay_step"]

    @property
    def handoff_file(self) -> str:
        return self.config_data["watcher"]["handoff_file"]

    @property
    def prompt_file(self) -> str:
        return self.config_data["watcher"]["prompt_file"]

    @property
    def roster_log_file(self) -> str:
        return self.config_data["watcher"]["roster_log_file"]

    @property
    def dry_run(self) -> bool:
        return self.config_data["watcher"].get("dry_run", False)

    @property
    def default_matrix(self) -> List[List[float]]:
        return self.config_data["dimensioning"]["default_matrix"]

    @property
    def webhook_matrix(self) -> List[List[float]]:
        return self.config_data["dimensioning"].get(
            "webhook_matrix", [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]]
        )

    @property
    def model_default(self) -> str:
        return self.config_data["models"].get("default", "gemini-2.5-flash")

    @property
    def model_succession(self) -> str:
        return self.config_data["models"].get("succession", self.model_default)

    @property
    def model_development(self) -> str:
        return self.config_data["models"].get("development", self.model_default)

    @property
    def model_review(self) -> str:
        return self.config_data["models"].get("review", self.model_default)

    @property
    def model_security(self) -> str:
        return self.config_data["models"].get("security", self.model_default)

    @property
    def advisor_enabled(self) -> bool:
        return self.config_data["advisor"].get("enabled", True)

    @property
    def advisor_nudge_turn(self) -> int:
        return self.config_data["advisor"].get("nudge_turn", 2)

    @property
    def advisor_nudge_text(self) -> str:
        return self.config_data["advisor"].get("nudge_text", "")

    @property
    def advisor_max_tokens(self) -> int:
        return self.config_data["advisor"].get("max_tokens", 2048)
