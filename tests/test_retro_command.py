"""Tests for the `retro` CLI command and format_retro_report()."""
import json
import os
from unittest.mock import patch
from self_governance.learning import format_retro_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EMPTY_STATE = {
    "sessions_distilled": 0,
    "avg_cycles_needed": 0.0,
    "success_rate": 0.0,
    "average_cycle_time": 0.0,
    "vulnerability_counts": 0,
    "last_approved_roster": [],
    "distillation_log": [],
    "matrix_tuning": {"scale_factor": 1.0},
    "runs_completed": 0,
}


def _invoke_retro(*extra_args, state=None):
    """Call handle_retro() directly, capturing stdout, with an optional mocked state."""
    import io
    from contextlib import redirect_stdout
    from self_governance import cli as cli_module
    from argparse import Namespace

    if state is None:
        state = EMPTY_STATE

    args = Namespace(as_json=False, export=None)
    for arg in extra_args:
        if arg == "--json":
            args.as_json = True
    # Parse --export FILE pairs
    arg_list = list(extra_args)
    if "--export" in arg_list:
        idx = arg_list.index("--export")
        args.export = arg_list[idx + 1]

    buf = io.StringIO()
    with patch("self_governance.learning.get_learning_state", return_value=state):
        with redirect_stdout(buf):
            cli_module.handle_retro(args)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Unit tests: format_retro_report()
# ---------------------------------------------------------------------------

class TestFormatRetroReport:
    def test_empty_state_returns_no_sessions_message(self):
        report = format_retro_report(EMPTY_STATE)
        assert "ASG Retrospective Report" in report
        assert "No sessions distilled yet" in report

    def test_report_contains_all_sections(self):
        state = {
            "sessions_distilled": 3,
            "avg_cycles_needed": 2.0,
            "success_rate": 1.0,
            "average_cycle_time": 45.0,
            "vulnerability_counts": 0,
            "last_approved_roster": ["Backend Wizard"],
            "distillation_log": [
                {
                    "roster": ["Backend Wizard"],
                    "cycles_needed": 2,
                    "final_temperature": 1.0,
                    "pattern": "Backend Wizard reached consensus in 2 cycles.",
                    "anti_pattern": "",
                },
            ],
            "matrix_tuning": {"scale_factor": 1.0},
            "runs_completed": 3,
        }
        report = format_retro_report(state)
        assert "Summary Metrics" in report
        assert "Recent Patterns" in report
        assert "Anti-Patterns" in report
        assert "Roster Evolution" in report
        assert "Recommendations" in report

    def test_high_avg_cycles_triggers_recommendation(self):
        state = {
            **EMPTY_STATE,
            "sessions_distilled": 5,
            "avg_cycles_needed": 6.5,
            "success_rate": 0.8,
            "average_cycle_time": 60.0,
        }
        report = format_retro_report(state)
        assert "High avg cycles" in report

    def test_vulnerability_count_triggers_recommendation(self):
        state = {
            **EMPTY_STATE,
            "sessions_distilled": 3,
            "avg_cycles_needed": 1.5,
            "success_rate": 1.0,
            "average_cycle_time": 30.0,
            "vulnerability_counts": 2,
        }
        report = format_retro_report(state)
        assert "security event" in report

    def test_low_success_rate_triggers_recommendation(self):
        state = {
            **EMPTY_STATE,
            "sessions_distilled": 10,
            "avg_cycles_needed": 2.0,
            "success_rate": 0.7,
            "average_cycle_time": 40.0,
        }
        report = format_retro_report(state)
        assert "Low success rate" in report

    def test_healthy_metrics_show_no_action_needed(self):
        state = {
            **EMPTY_STATE,
            "sessions_distilled": 3,
            "avg_cycles_needed": 2.0,
            "success_rate": 1.0,
            "average_cycle_time": 30.0,
        }
        report = format_retro_report(state)
        assert "All metrics healthy" in report

    def test_anti_pattern_deduplication_and_count(self):
        state = {
            **EMPTY_STATE,
            "sessions_distilled": 4,
            "avg_cycles_needed": 2.0,
            "success_rate": 1.0,
            "distillation_log": [
                {
                    "roster": [],
                    "cycles_needed": 5,
                    "final_temperature": 1.0,
                    "pattern": "",
                    "anti_pattern": "High cycle count indicates initial roster misalignment.",
                },
                {
                    "roster": [],
                    "cycles_needed": 5,
                    "final_temperature": 1.0,
                    "pattern": "",
                    "anti_pattern": "High cycle count indicates initial roster misalignment.",
                },
                {
                    "roster": [],
                    "cycles_needed": 2,
                    "final_temperature": 1.0,
                    "pattern": "Fast consensus",
                    "anti_pattern": "",
                },
            ],
        }
        report = format_retro_report(state)
        assert "(2x)" in report
        assert "misalignment" in report

    def test_no_state_arg_loads_from_disk(self, tmp_path, monkeypatch):
        """format_retro_report(None) must fall back to get_learning_state()."""
        monkeypatch.chdir(tmp_path)
        import self_governance.learning as lm
        monkeypatch.setattr(lm, "LEARNING_STATE_FILE", str(tmp_path / ".learning_state.json"))
        report = format_retro_report()
        assert "ASG Retrospective Report" in report


# ---------------------------------------------------------------------------
# Integration tests: handle_retro() via direct invocation
# ---------------------------------------------------------------------------

class TestRetroCliCommand:
    def test_retro_prints_report(self):
        output = _invoke_retro(state=EMPTY_STATE)
        assert "ASG Retrospective Report" in output

    def test_retro_export_writes_file(self, tmp_path):
        export_file = str(tmp_path / "retro.md")
        _invoke_retro("--export", export_file, state=EMPTY_STATE)
        assert os.path.exists(export_file)
        with open(export_file) as f:
            content = f.read()
        assert "ASG Retrospective Report" in content

    def test_retro_json_outputs_valid_json(self):
        output = _invoke_retro("--json", state=EMPTY_STATE)
        parsed = json.loads(output)
        assert "sessions_distilled" in parsed

    def test_retro_json_contains_all_state_keys(self):
        state = {**EMPTY_STATE, "sessions_distilled": 5}
        output = _invoke_retro("--json", state=state)
        parsed = json.loads(output)
        assert parsed["sessions_distilled"] == 5
