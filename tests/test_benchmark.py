import json
import os
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


def test_run_one_isolated_uses_a_distinct_tempdir_per_call(monkeypatch):
    """The actual isolation mechanism, tested directly and deterministically:
    each call to _run_one_isolated chdirs into its own fresh tempdir and
    restores the original cwd after. This is what prevents concurrent reps
    from colliding on identical target_file/bench_test_*.py filenames --
    tested here without going through Docker or a real process pool, since
    neither is needed to verify this specific guarantee, and both would
    make the test dependent on sandbox/network availability."""
    import self_governance.benchmark as bm

    seen_cwds = []

    def fake_mode_fn(task, api_key):
        seen_cwds.append(os.getcwd())
        return {"passed": True, "latency_sec": 0.0, "estimated_cost_usd": 0.0}

    monkeypatch.setattr(bm, "run_baseline_mode", fake_mode_fn)
    start_cwd = os.getcwd()

    bm._run_one_isolated(_TINY_TASK, "baseline", 0, None)
    bm._run_one_isolated(_TINY_TASK, "baseline", 1, None)

    assert len(seen_cwds) == 2
    assert seen_cwds[0] != seen_cwds[1], "two calls reused the same tempdir"
    assert start_cwd not in seen_cwds
    assert os.getcwd() == start_cwd, "cwd was not restored after each call"
    # Cleaned up afterward, not left behind on every sweep
    assert not os.path.exists(seen_cwds[0])
    assert not os.path.exists(seen_cwds[1])


def test_run_benchmark_parallel_dispatches_every_unit_exactly_once(monkeypatch):
    """Structural correctness of the concurrent dispatch, independent of
    whatever the sandboxed test outcome happens to be in this environment
    (Docker/network availability varies between local and CI, so pass/fail
    of the sandboxed test itself isn't a portable thing to assert on here --
    see test_run_one_isolated_uses_a_distinct_tempdir_per_call for the
    actual isolation-mechanism proof). This test proves the concurrent
    fan-out/fan-in bookkeeping is correct: exactly the requested units run,
    each exactly once, via a real ProcessPoolExecutor."""
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
    for mode_results in (results["task_tiny"]["baseline"], results["task_tiny"]["asg"]):
        for r in mode_results:
            assert "error" not in r, f"worker raised an unexpected exception: {r}"


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


def test_run_benchmark_parallel_resume_skips_completed_units(tmp_path, monkeypatch):
    """A checkpoint file lets a sweep survive being cut off (e.g. a daily
    quota) and pick up only the unfinished units next time, instead of
    re-running everything from scratch."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda: [_TINY_TASK]
    )
    checkpoint = tmp_path / "checkpoint.jsonl"

    # First run: only 1 rep, writes 2 units (baseline+asg) to the checkpoint.
    run_benchmark_parallel(
        api_key=None, reps=1, workers=2, resume_path=str(checkpoint)
    )
    assert len(checkpoint.read_text().strip().splitlines()) == 2

    # Second run asks for 2 reps -- rep 0 for both modes is already done,
    # so only 2 new units (rep 1, baseline+asg) should actually execute.
    seen = []
    results = run_benchmark_parallel(
        api_key=None,
        reps=2,
        workers=2,
        resume_path=str(checkpoint),
        on_result=seen.append,
    )
    assert len(seen) == 2, "resume should have skipped the already-done units"
    assert len(results["task_tiny"]["baseline"]) == 2
    assert len(results["task_tiny"]["asg"]) == 2
    assert len(checkpoint.read_text().strip().splitlines()) == 4


def test_run_benchmark_parallel_resume_retries_error_outcomes(tmp_path, monkeypatch):
    """An outcome with an 'error' (e.g. a quota 429 the adapter's own
    retries couldn't outlast) must NOT be treated as done on resume --
    otherwise a quota cutoff mid-sweep would permanently and silently
    drop every unit it failed, rather than retrying them once the quota
    resets."""
    checkpoint = tmp_path / "checkpoint.jsonl"
    checkpoint.write_text(
        json.dumps(
            {
                "task_id": "task_tiny",
                "mode": "baseline",
                "rep": 0,
                "result": {
                    "passed": False,
                    "latency_sec": 0.0,
                    "estimated_cost_usd": 0.0,
                    "error": "quota exceeded",
                },
            }
        )
        + "\n"
    )
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda: [_TINY_TASK]
    )

    seen = []
    run_benchmark_parallel(
        api_key=None, reps=1, workers=2, resume_path=str(checkpoint), on_result=seen.append
    )
    ran_keys = {(o["task_id"], o["mode"], o["rep"]) for o in seen}
    assert ("task_tiny", "baseline", 0) in ran_keys, (
        "a previously-errored unit must be retried, not skipped as done"
    )


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
