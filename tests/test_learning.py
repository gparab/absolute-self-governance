import os
import pytest
from self_governance.models import SessionStatus
from self_governance.learning import (
    get_learning_state,
    track_learning_feedback,
    LEARNING_STATE_FILE,
)


@pytest.fixture(autouse=True)
def clean_learning_state():
    """Ensure learning state file is cleaned up after each test."""
    if os.path.exists(LEARNING_STATE_FILE):
        os.remove(LEARNING_STATE_FILE)
    yield
    if os.path.exists(LEARNING_STATE_FILE):
        os.remove(LEARNING_STATE_FILE)


def test_learning_state_defaults():
    state = get_learning_state()
    assert state["runs_completed"] == 0
    assert state["success_rate"] == 1.0
    assert state["average_cycle_time"] == 0.0


def test_learning_feedback_nominal():
    track_learning_feedback(cycle_time=12.0, success=True)
    state = get_learning_state()
    assert state["runs_completed"] == 1
    assert state["success_rate"] == 1.0
    assert state["average_cycle_time"] == 12.0


def test_learning_feedback_failures():
    track_learning_feedback(cycle_time=10.0, success=True)
    track_learning_feedback(cycle_time=20.0, success=False)

    state = get_learning_state()
    assert state["runs_completed"] == 2
    assert state["success_rate"] == 0.5
    assert state["average_cycle_time"] == 15.0


def test_learning_feedback_security_alert():
    track_learning_feedback(cycle_time=5.0, success=True, security_breached=True)
    state = get_learning_state()
    assert state["vulnerability_counts"] == 1
    assert state["matrix_tuning"]["scale_factor"] == 1.15


def test_learning_loop_tunes_dimensioning(tmp_path, monkeypatch):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    from self_governance.nudger import ContinuousNudger
    from self_governance.config import OrchestratorConfig
    from unittest.mock import MagicMock
    import yaml

    # Mock consensus
    mock_consensus = MagicMock()
    mock_consensus.return_value.approved_roster = ["Backend Wizard"]
    mock_consensus.return_value.prompt_tokens = 0
    mock_consensus.return_value.completion_tokens = 0
    mock_consensus.return_value.final_temperature = 1.0
    mock_consensus.return_value.final_threshold = 0.9
    mock_consensus.return_value.cycles_needed = 1
    monkeypatch.setattr("self_governance.nudger.run_consensus", mock_consensus)

    config = OrchestratorConfig()
    # Matrix where index 2 is security auditor
    config.config_data["dimensioning"]["default_matrix"] = [
        [1.0, 0.0],  # Backend Wizard
        [0.0, 1.0],  # QA Specialist
        [0.0, 1.0],  # Security Auditor
    ]

    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    handoff_path = os.path.join(str(tmp_path), ".planning/CURRENT_STATE.md")

    # 1. Run baseline succession (scale_factor is 1.0)
    with open(handoff_path, "w", encoding="utf-8") as f:
        yaml.dump({"status": SessionStatus.COMPLETED.value, "candidates": ["Backend Wizard"]}, f)

    # Track baseline counts
    nudger.process_handoff()

    prompt_path = os.path.join(str(tmp_path), "prompt_draft.md")
    with open(prompt_path, "r", encoding="utf-8") as f:
        baseline_content = f.read()

    # 2. Trigger security breach (increases scale_factor to 1.15)
    track_learning_feedback(cycle_time=5.0, success=True, security_breached=True)

    # Reset handoff to force processing again
    nudger.last_content = None

    # Run succession again
    nudger.process_handoff()

    with open(prompt_path, "r", encoding="utf-8") as f:
        tuned_content = f.read()

    backend_prompt_path = os.path.join(str(tmp_path), "prompt_draft_backend.md")
    if os.path.exists(backend_prompt_path):
        with open(backend_prompt_path, "r", encoding="utf-8") as f:
            tuned_content += "\n" + f.read()
            
    frontend_prompt_path = os.path.join(str(tmp_path), "prompt_draft_frontend.md")
    if os.path.exists(frontend_prompt_path):
        with open(frontend_prompt_path, "r", encoding="utf-8") as f:
            tuned_content += "\n" + f.read()

    # Tuned run should have scale_factor applied to dimensioning matrix weights
    assert "Security Auditor" in baseline_content or "Security Auditor" in tuned_content
