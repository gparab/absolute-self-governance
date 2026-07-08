import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("self_governance.learning")

LEARNING_STATE_FILE = ".learning_state.json"


def get_learning_state() -> Dict[str, Any]:
    """Retrieve the current learning logs and model state."""
    if os.path.exists(LEARNING_STATE_FILE):
        try:
            with open(LEARNING_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load learning state: %s. Re-initializing.", e)

    return {
        "runs_completed": 0,
        "success_rate": 1.0,
        "average_cycle_time": 0.0,
        "vulnerability_counts": 0,
        "matrix_tuning": {"scale_factor": 1.0},
    }


def save_learning_state(state: Dict[str, Any]) -> None:
    """Save the updated learning logs and model state."""
    try:
        with open(LEARNING_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.error("Failed to save learning state: %s", e)


def track_learning_feedback(
    cycle_time: float, success: bool, security_breached: bool = False
) -> None:
    """
    Adjust dimensioning matrix scaling factors based on execution metrics.
    """
    state = get_learning_state()

    # Calculate rolling averages
    n = state["runs_completed"]
    state["runs_completed"] = n + 1

    # Update success rate
    prev_success_sum = state["success_rate"] * n
    state["success_rate"] = (prev_success_sum + (1.0 if success else 0.0)) / (n + 1)

    # Update cycle time
    prev_cycle_sum = state["average_cycle_time"] * n
    state["average_cycle_time"] = (prev_cycle_sum + cycle_time) / (n + 1)

    if security_breached:
        state["vulnerability_counts"] += 1
        # Increase security agent staffing weights by tuning the scale factor
        state["matrix_tuning"]["scale_factor"] += 0.15
        logger.info(
            "Security risk logged. Scaling factor tuned up to %s",
            state["matrix_tuning"]["scale_factor"],
        )

    save_learning_state(state)
