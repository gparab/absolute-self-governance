import os
import json
import pytest
from self_governance.learning import (
    get_learning_state,
    save_learning_state,
    track_learning_feedback,
    LEARNING_STATE_FILE
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
