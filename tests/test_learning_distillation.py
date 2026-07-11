"""Tests for cross-session learning distillation."""
from unittest.mock import patch


class TestDistillSession:
    def test_distill_creates_log_entry(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, get_learning_state
            distill_session(
                session_result=None,
                roster=["Backend Wizard", "QA Specialist"],
                cycles=2,
                temperature=1.1,
            )
            state = get_learning_state()
            assert state["sessions_distilled"] == 1
            assert state["avg_cycles_needed"] == 2.0
            assert state["last_approved_roster"] == ["Backend Wizard", "QA Specialist"]
            assert len(state["distillation_log"]) == 1
            assert "Backend Wizard" in state["distillation_log"][0]["pattern"]

    def test_distill_multiple_sessions_rolling_average(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, get_learning_state
            distill_session(None, ["A"], 2, 1.0)
            distill_session(None, ["B"], 4, 1.2)
            state = get_learning_state()
            assert state["sessions_distilled"] == 2
            assert abs(state["avg_cycles_needed"] - 3.0) < 0.01

    def test_distill_log_capped_at_50(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, get_learning_state
            for i in range(55):
                distill_session(None, [f"Agent{i}"], 1, 1.0)
            state = get_learning_state()
            assert len(state["distillation_log"]) == 50

    def test_anti_pattern_flagged_for_high_cycle_count(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, get_learning_state
            distill_session(None, ["Slow Agent"], 8, 2.0)
            state = get_learning_state()
            entry = state["distillation_log"][0]
            assert "misalignment" in entry["anti_pattern"]


class TestRestoreSessionContext:
    def test_restore_empty_state_returns_defaults(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import restore_session_context
            ctx = restore_session_context()
            assert ctx["sessions_distilled"] == 0
            assert ctx["last_approved_roster"] == []
            assert ctx["recent_patterns"] == []

    def test_restore_after_distillation(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, restore_session_context
            distill_session(None, ["Backend Wizard"], 3, 1.2)
            ctx = restore_session_context()
            assert ctx["sessions_distilled"] == 1
            assert ctx["last_approved_roster"] == ["Backend Wizard"]
            assert len(ctx["recent_patterns"]) == 1
            assert "Backend Wizard" in ctx["recent_patterns"][0]

    def test_restore_returns_last_5_patterns(self, tmp_path):
        state_file = str(tmp_path / ".learning_state.json")
        with patch("self_governance.learning.LEARNING_STATE_FILE", state_file):
            from self_governance.learning import distill_session, restore_session_context
            for i in range(8):
                distill_session(None, [f"Agent{i}"], 1, 1.0)
            ctx = restore_session_context()
            assert len(ctx["recent_patterns"]) == 5  # capped at 5
