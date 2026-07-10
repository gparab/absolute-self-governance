import sys
from unittest.mock import patch
from self_governance.benchmark import (
    run_benchmark,
    run_benchmark_parallel,
    load_benchmark_tasks,
)
from self_governance.cli import main

_TINY_TASK = {
    "id": "task_tiny",
    "name": "Tiny",
    "description": "irrelevant -- no API key means this never reaches the network",
    "test_code": "def test_x():\n    assert True\n",
    "target_file": "tiny.py",
}


def test_load_benchmark_tasks():
    tasks = load_benchmark_tasks()
    assert len(tasks) == 6
    ids = {t["id"] for t in tasks}
    assert ids == {
        "task_secure_reader",
        "task_thread_safe_cache",
        "task_reverse_string",
        "task_email_validator",
        "task_lru_cache",
        "task_retry_backoff",
    }


def test_run_benchmark_mocked(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Mock execute_development & execute_tests to return clean results
    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "execute_development",
        lambda self, agents, plan: {"status": "completed", "written_files": []},
    )
    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "execute_tests",
        lambda self, agents, changes, test_target=None: {"status": "completed"},
    )

    results = run_benchmark(api_key=None)
    assert "task_secure_reader" in results
    assert results["task_secure_reader"]["baseline"]["passed"] is True
    assert results["task_secure_reader"]["asg"]["passed"] is True


def test_cli_benchmark(monkeypatch, capsys):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "execute_development",
        lambda self, agents, plan: {"status": "completed", "written_files": []},
    )
    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "execute_tests",
        lambda self, agents, changes, test_target=None: {"status": "completed"},
    )

    test_args = ["self-governance", "benchmark"]
    with patch.object(sys, "argv", test_args):
        main()

    captured = capsys.readouterr()
    assert "Secure File Reader" in captured.out
    assert "Thread Safe Cache" in captured.out


def test_run_benchmark_parallel_isolates_concurrent_reps(monkeypatch):
    """Each (task, mode, rep) unit must run in its own process/tempdir --
    if isolation were broken, concurrent reps of the same task would race
    on the same target_file/bench_test_*.py filenames and corrupt each
    other's results. Verify every requested unit comes back exactly once,
    with no crashes, using a single tiny task to keep this fast (no Docker
    spin-up storm across all 6 real tasks)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda: [_TINY_TASK]
    )

    seen = []
    results = run_benchmark_parallel(
        api_key=None, reps=2, workers=2, on_result=seen.append
    )

    assert set(results.keys()) == {"task_tiny"}
    assert len(results["task_tiny"]["baseline"]) == 2
    assert len(results["task_tiny"]["asg"]) == 2
    # 1 task x 2 modes x 2 reps = 4 units, each firing on_result exactly once
    assert len(seen) == 4
    seen_keys = {(o["task_id"], o["mode"], o["rep"]) for o in seen}
    assert seen_keys == {
        ("task_tiny", "baseline", 0),
        ("task_tiny", "baseline", 1),
        ("task_tiny", "asg", 0),
        ("task_tiny", "asg", 1),
    }
    # The tiny task's test never imports from the (never-written, no-key)
    # target module, so every rep should deterministically pass -- if
    # concurrent reps were racing on shared filenames, this would be flaky
    # instead of uniformly True across all 4 units.
    for mode_results in (results["task_tiny"]["baseline"], results["task_tiny"]["asg"]):
        for r in mode_results:
            assert r["passed"] is True
            assert "error" not in r  # no Python exception inside the worker


def test_run_one_isolated_captures_worker_exception(monkeypatch):
    """A worker-side exception (real API error, sandbox failure, whatever)
    must come back as a result dict with an error field, not crash the
    whole pool -- one bad task/rep/mode shouldn't kill an entire sweep."""
    import self_governance.benchmark as bm

    def boom(task, api_key):
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(bm, "run_baseline_mode", boom)
    outcome = bm._run_one_isolated(_TINY_TASK, "baseline", 0, None)

    assert outcome["result"]["passed"] is False
    assert outcome["result"]["error"] == "simulated worker failure"


def test_cli_benchmark_parallel(monkeypatch, capsys):
    """The --reps > 1 CLI path is a genuinely different code path from the
    default (routes to run_benchmark_parallel, not run_benchmark) and needs
    its own coverage, not just the library-level test."""
    import sys as _sys

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda: [_TINY_TASK]
    )

    test_args = ["self-governance", "benchmark", "--reps", "2", "--workers", "2"]
    with patch.object(_sys, "argv", test_args):
        main()

    out = capsys.readouterr().out
    assert "Running 2 reps/task/mode with 2 concurrent workers" in out
    assert "task_tiny baseline rep 1/2" in out or "task_tiny baseline rep 2/2" in out
    assert "task_tiny" in out and "baseline" in out and "asg" in out


def test_run_benchmark_parallel_workers_clamped():
    """workers is clamped to a sane range so a typo (e.g. workers=1000)
    can't fork-bomb the host."""
    import self_governance.benchmark as bm

    called = {}

    class FakeExecutor:
        def __init__(self, max_workers=None):
            called["max_workers"] = max_workers

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            class F:
                def result(self_inner):
                    return fn(*args)

            return F()

    import unittest.mock as mock

    with mock.patch.object(bm, "ProcessPoolExecutor", FakeExecutor), \
         mock.patch.object(bm, "load_benchmark_tasks", lambda: [_TINY_TASK]), \
         mock.patch.object(bm, "as_completed", lambda futures: futures):
        bm.run_benchmark_parallel(api_key=None, reps=1, workers=999)

    assert called["max_workers"] == 16
