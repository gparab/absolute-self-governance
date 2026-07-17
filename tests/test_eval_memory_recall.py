from telemetry.eval_memory_recall import run_checks


def test_memory_recall_harness_all_checks_pass():
    checks = run_checks()
    failed = [name for name, passed, _ in checks if not passed]
    assert not failed, f"memory recall harness regressed: {failed}"
    assert len(checks) == 5
