"""Shadow Logging and Human-In-The-Loop Verification module.

Supports structured log auditing (log_shadow_event) and execution halts
for manual approval if confidence thresholds are not met.
"""

import os
import json
import yaml
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List

logger = logging.getLogger("self_governance.shadow_logging")


def log_shadow_event(event_type: str, details: Dict[str, Any], log_filepath: str = "shadow_log.json") -> None:
    """Appends a structured event log to a deterministic JSON or YAML file.

    Args:
        event_type: Name category of the event to log.
        details: Extra key-value dictionary metadata.
        log_filepath: Path destination target file path.
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event_type": event_type,
        "details": details
    }

    logs: List[Dict[str, Any]] = []
    if os.path.exists(log_filepath):
        try:
            if log_filepath.endswith((".yaml", ".yml")):
                with open(log_filepath, "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f)
                    if isinstance(content, list):
                        logs = content
            else:
                with open(log_filepath, "r", encoding="utf-8") as f:
                    content = json.load(f)
                    if isinstance(content, list):
                        logs = content
        except Exception as e:
            logger.warning("Failed to read shadow log file: %s. Re-initializing log list.", e)

    logs.append(event)

    try:
        dir_name = os.path.dirname(log_filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)

        if log_filepath.endswith((".yaml", ".yml")):
            with open(log_filepath, "w", encoding="utf-8") as f:
                yaml.safe_dump(logs, f)
        else:
            with open(log_filepath, "w", encoding="utf-8") as f:
                json.dump(logs, f, indent=2)
    except Exception as e:
        logger.error("Failed to write shadow log event: %s", e)


def check_confidence_and_prompt(confidence_score: float, threshold: float = 0.7, hitl_filepath: str = "awaiting_hitl.json") -> bool:
    """Halts execution and writes an approval request if confidence score < threshold.

    Args:
        confidence_score: The current confidence value.
        threshold: The target required threshold value.
        hitl_filepath: Output JSON path for approval metadata.

    Returns:
        bool: True if confidence meets threshold, False if halted for HITL approval.
    """
    if confidence_score < threshold:
        request_data = {
            "confidence_score": confidence_score,
            "threshold": threshold,
            "status": "AWAITING_APPROVAL",
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            dir_name = os.path.dirname(hitl_filepath)
            if dir_name:
                os.makedirs(dir_name, exist_ok=True)
            with open(hitl_filepath, "w", encoding="utf-8") as f:
                json.dump(request_data, f, indent=2)
            logger.warning("Confidence score %s below threshold %s. Halted for HITL approval.", confidence_score, threshold)
        except Exception as e:
            logger.error("Failed to write HITL file: %s", e)
        return False
    return True

