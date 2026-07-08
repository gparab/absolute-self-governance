import os
import logging
import yaml
import copy
from typing import Dict, Any, List

logger = logging.getLogger("self_governance.config")

DEFAULT_CONFIG = {
    "consensus": {
        "buffer_limit": 3,
        "target_threshold": 9.0,
        "initial_temperature": 1.0,
        "temperature_step": 0.1,
        "decay_step": 0.5
    },
    "watcher": {
        "handoff_file": "handoff.md",
        "prompt_file": "prompt_draft.md",
        "roster_log_file": "roster_rotation_log.md",
        "dry_run": False
    },
    "dimensioning": {
        "default_matrix": [
            [1.0, 0.5],
            [0.0, 1.0]
        ],
        "webhook_matrix": [
            [1.0, 0.0],
            [0.0, 1.0],
            [0.5, 0.5],
            [0.2, 0.8]
        ]
    }
}

class OrchestratorConfig:
    """
    Configuration manager for the Self-Governance orchestrator.
    """
    def __init__(self, config_path: str = None) -> None:
        self.config_data = copy.deepcopy(DEFAULT_CONFIG)
        if config_path and os.path.exists(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    user_data = yaml.safe_load(f)
                    if user_data:
                        self._merge_config(self.config_data, user_data)
                logger.info("Loaded configuration from %s", config_path)
            except Exception as e:
                logger.warning("Failed to load config from %s: %s. Using defaults.", config_path, e)

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
        return self.config_data["dimensioning"].get("webhook_matrix", [
            [1.0, 0.0],
            [0.0, 1.0],
            [0.5, 0.5],
            [0.2, 0.8]
        ])
