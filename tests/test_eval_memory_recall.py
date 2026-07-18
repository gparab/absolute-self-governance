from unittest.mock import patch

import telemetry.eval_memory_recall as eval_memory_recall
from telemetry.eval_memory_recall import main, run_checks


def test_memory_recall_harness_all_checks_pass():
    checks = run_checks()
    failed = [name for name, passed, _ in checks if not passed]
    assert not failed, f"memory recall harness regressed: {failed}"
    assert len(checks) == 11


def test_main_fires_regression_alert_and_exits_nonzero_on_failure(capsys):
    fake_checks = [("recall check", False, "context detail")]
    with patch.object(eval_memory_recall, "run_checks", return_value=fake_checks):
        exit_code = main()

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "ALERT [memory_recall_regression]" in out
    assert "recall check" in out
