import json
import os
import sys
from unittest.mock import patch
from self_governance.benchmark import (
    run_benchmark,
    run_benchmark_parallel,
    run_asg_mode,
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


_TASK_A = {**_TINY_TASK, "id": "task_a", "name": "A"}
_TASK_B = {**_TINY_TASK, "id": "task_b", "name": "B"}


def test_run_benchmark_parallel_task_ids_restricts_to_subset(monkeypatch):
    """--tasks / task_ids must run only the requested subset -- this is
    what lets a rep-heavy sweep concentrate spend on the tasks that show
    variance instead of re-running ceiling-bound tasks for no
    statistical benefit (see the post-validation improvement plan)."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks",
        lambda source=None: [_TASK_A, _TASK_B],
    )

    results = run_benchmark_parallel(
        api_key=None, reps=1, workers=2, task_ids=["task_a"]
    )

    assert set(results.keys()) == {"task_a"}


def test_run_benchmark_parallel_unknown_task_id_raises(monkeypatch):
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks",
        lambda source=None: [_TASK_A],
    )
    try:
        run_benchmark_parallel(api_key=None, reps=1, task_ids=["not_a_real_task"])
        assert False, "expected ValueError"
    except ValueError as e:
        assert "not_a_real_task" in str(e)


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


def test_model_param_threads_through_to_adapter_construction(monkeypatch):
    """--model must reach every stage of the adapter (default/development/
    review/security), not just one -- otherwise baseline and ASG could
    silently run different stages against different models within the
    same sweep, invalidating the comparison."""
    from self_governance.benchmark import run_baseline_mode

    captured = {}

    class FakeAdapter:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def execute_development(self, agents, plan):
            return {"written_files": []}

        def execute_tests(self, agents, changes, test_target=None):
            return {"status": "completed"}

        def get_billing_metrics(self):
            return {"estimated_cost_usd": 0.0}

    monkeypatch.setattr(
        "self_governance.gemini_adapter.GeminiExecutionAdapter", FakeAdapter
    )
    run_baseline_mode(_TINY_TASK, api_key=None, model="some-custom-model")

    assert captured["model_default"] == "some-custom-model"
    assert captured["model_development"] == "some-custom-model"
    assert captured["model_review"] == "some-custom-model"
    assert captured["model_security"] == "some-custom-model"


def _mock_asg_stages(monkeypatch, dev_fn, test_fn):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setattr(GeminiExecutionAdapter, "execute_development", dev_fn)
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_tests", test_fn)
    monkeypatch.setattr(
        GeminiExecutionAdapter, "review_code", lambda self, a, c: {"status": "completed"}
    )
    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "run_security_scan",
        lambda self, a, c: {"status": "completed"},
    )


def test_asg_rotates_perspectives_and_stops_on_first_pass(monkeypatch, tmp_path):
    """Each attempt must be led by a DIFFERENT specialist persona, carry
    the acceptance tests, feed the prior failure output forward, and the
    loop must stop on the first sandbox pass -- perspective diversity +
    repair feedback + early exit as one mechanism."""
    monkeypatch.chdir(tmp_path)
    calls = {"dev": 0, "test": 0}
    leads = []

    def fake_dev(self, agents, plan):
        calls["dev"] += 1
        assert "acceptance_tests" in plan, "every attempt must see the tests"
        leads.append(plan["lead_perspective"])
        assert len(agents) == 1 and agents[0].role == plan["lead_perspective"]
        if calls["dev"] > 1:
            assert "previous_attempt_failed_tests" in plan
            assert "1 failed" in plan["previous_attempt_failed_tests"]
        return {"status": "completed", "written_files": []}

    def fake_tests(self, agents, changes, test_target=None):
        calls["test"] += 1
        if calls["test"] == 1:
            return {"status": "failed", "raw_test_output": "1 failed: test_x"}
        return {"status": "completed", "raw_test_output": "1 passed"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)

    assert res["passed"] is True
    assert res["attempts"] == 2
    assert calls["dev"] == 2 and calls["test"] == 2
    assert leads == ["Backend Wizard", "QA Specialist"], (
        "the second attempt must rotate to a different persona"
    )


def test_asg_detects_stalled_attempt_with_identical_failing_test_set(monkeypatch, tmp_path):
    """A rewrite that fails the exact same tests as the previous attempt made
    no progress -- distinct from a rewrite that fails differently (looper's
    no-progress-signature rule, July 2026 topic-page batch)."""
    monkeypatch.chdir(tmp_path)
    calls = {"dev": 0, "test": 0}

    def fake_dev(self, agents, plan):
        calls["dev"] += 1
        return {"status": "completed", "written_files": []}

    def fake_tests(self, agents, changes, test_target=None):
        calls["test"] += 1
        if calls["test"] <= 2:
            # Identical failing-test set both times -- no progress made.
            return {"status": "failed", "raw_test_output": "FAILED tests/test_x.py::test_boundary - AssertionError"}
        return {"status": "completed", "raw_test_output": "1 passed"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)

    assert res["passed"] is True
    assert res["stalled_attempts"] == 1


def test_asg_does_not_flag_stall_when_failing_tests_differ(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    calls = {"dev": 0, "test": 0}

    def fake_dev(self, agents, plan):
        calls["dev"] += 1
        return {"status": "completed", "written_files": []}

    def fake_tests(self, agents, changes, test_target=None):
        calls["test"] += 1
        if calls["test"] == 1:
            return {"status": "failed", "raw_test_output": "FAILED tests/test_x.py::test_boundary - AssertionError"}
        if calls["test"] == 2:
            return {"status": "failed", "raw_test_output": "FAILED tests/test_x.py::test_other - ValueError"}
        return {"status": "completed", "raw_test_output": "1 passed"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)

    assert res["passed"] is True
    assert res["stalled_attempts"] == 0


def test_asg_no_stall_signature_for_unparseable_raw_output(monkeypatch, tmp_path):
    """Raw output that doesn't match the FAILED-line format yields an empty
    signature, which must never be treated as a match against itself --
    otherwise every non-pytest-formatted failure would falsely flag as a
    stall on the very first repeat."""
    monkeypatch.chdir(tmp_path)
    calls = {"dev": 0, "test": 0}

    def fake_dev(self, agents, plan):
        calls["dev"] += 1
        return {"status": "completed", "written_files": []}

    def fake_tests(self, agents, changes, test_target=None):
        calls["test"] += 1
        return {"status": "failed", "raw_test_output": "still failing"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)

    assert res["passed"] is False
    assert res["stalled_attempts"] == 0


def test_asg_caps_at_three_attempts(monkeypatch, tmp_path):
    """A persistently failing unit stops after the roster is exhausted
    (3 attempts) -- honest failure, not an infinite retry burn."""
    monkeypatch.chdir(tmp_path)
    calls = {"dev": 0, "test": 0}

    def fake_dev(self, agents, plan):
        calls["dev"] += 1
        return {"status": "completed", "written_files": []}

    def fake_tests(self, agents, changes, test_target=None):
        calls["test"] += 1
        return {"status": "failed", "raw_test_output": "still failing"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)

    assert res["passed"] is False
    assert res["attempts"] == 3
    assert calls["dev"] == 3
    assert calls["test"] == 3


def test_failure_classification_distinguishes_infra_from_quality(monkeypatch, tmp_path):
    """The failure taxonomy must separate 'the environment is broken'
    from 'the generated code is bad' -- three sweeps in this repo's
    history were invalidated by infra failures recorded as ordinary
    FAILs (revoked key, Docker down, broken shim), each caught only by
    a human noticing impossible aggregate numbers."""
    from self_governance.benchmark import _classify_failure

    # Passed -> no class
    assert _classify_failure(True, ["f.py"], {"raw_test_output": "1 passed"}) is None
    # Docker daemon down -> sandbox_error, regardless of written files
    assert (
        _classify_failure(
            False, ["f.py"], {"output": "Cannot connect to the Docker daemon at unix://..."}
        )
        == "sandbox_error"
    )
    assert (
        _classify_failure(
            False, [], {"output": "Containerized test execution failed: X. Host execution fallback is disabled for security."}
        )
        == "sandbox_error"
    )
    # Generation produced nothing runnable (API failure / format failure)
    assert _classify_failure(False, [], {"raw_test_output": "collected 0 items"}) == "no_files_written"
    # Real quality failure: code existed, ran, failed
    assert (
        _classify_failure(False, ["f.py"], {"raw_test_output": "1 failed: test_x"})
        == "tests_failed"
    )


def test_mode_results_carry_failure_class(monkeypatch, tmp_path):
    """Both mode functions must record failure_class so the analyzer can
    flag infrastructure-contaminated datasets."""
    monkeypatch.chdir(tmp_path)

    def fake_dev(self, agents, plan):
        return {"status": "completed", "written_files": ["out.py"]}

    def fake_tests(self, agents, changes, test_target=None):
        return {"status": "failed", "raw_test_output": "1 failed"}

    _mock_asg_stages(monkeypatch, fake_dev, fake_tests)
    res = run_asg_mode(_TINY_TASK, api_key=None)
    assert res["failure_class"] == "tests_failed"

    from self_governance.benchmark import run_baseline_mode

    res_b = run_baseline_mode(_TINY_TASK, api_key=None)
    assert res_b["failure_class"] == "tests_failed"


def _fake_sandbox_error_unit(task, mode, rep, api_key, model=None):
    # Module-level: ProcessPoolExecutor's spawn start method requires
    # picklable (top-level) callables.
    return {
        "task_id": task["id"],
        "mode": mode,
        "rep": rep,
        "result": {
            "passed": False,
            "failure_class": "sandbox_error",
            "latency_sec": 0.1,
            "estimated_cost_usd": 0.0,
        },
    }


def test_sweep_aborts_on_consecutive_sandbox_errors(monkeypatch, tmp_path):
    """A broken sandbox can never produce valid data -- the sweep must
    trip its circuit breaker on consecutive sandbox_error units instead
    of burning the whole quota on invalid results (the Docker-down
    incident produced 96 invalid units before a human caught it)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda source=None: [_TINY_TASK]
    )
    monkeypatch.setattr(
        "self_governance.benchmark._run_one_isolated", _fake_sandbox_error_unit
    )

    seen = []
    run_benchmark_parallel(api_key=None, reps=50, workers=1, on_result=seen.append)

    # breaker threshold is workers*2 = 2; the sweep must stop early,
    # not run all 100 doomed units
    assert len(seen) < 100, f"sweep did not abort on sandbox errors, ran {len(seen)} units"


def test_execute_development_reformat_retry_recovers_unparseable_output(
    monkeypatch, tmp_path
):
    """When a model returns prose instead of the JSON contract, one
    reformat call must recover it instead of silently writing zero
    files (spec R2 -- observed live as ASG's dominant failure mode)."""
    monkeypatch.chdir(tmp_path)
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    adapter = GeminiExecutionAdapter(api_key="test-key")
    responses = iter(
        [
            "Sure! Here's my implementation:\ndef f(): return 1",
            json.dumps(
                {
                    "explanation": "reformatted",
                    "written_files": [
                        {"filepath": "recovered.py", "content": "def f(): return 1\n"}
                    ],
                }
            ),
        ]
    )
    monkeypatch.setattr(
        GeminiExecutionAdapter,
        "_call_gemini_and_track",
        lambda self, *a, **kw: next(responses),
    )

    res = adapter.execute_development([], {"task": "write f"})

    assert res["written_files"], "reformat retry should have recovered the files"
    assert os.path.exists("recovered.py")


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

    def fake_mode_fn(task, api_key, model=None):
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
        "self_governance.benchmark.load_benchmark_tasks", lambda source=None: [_TINY_TASK]
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

    def boom(task, api_key, model=None):
        raise RuntimeError("simulated worker failure")

    monkeypatch.setattr(bm, "run_baseline_mode", boom)
    outcome = bm._run_one_isolated(_TINY_TASK, "baseline", 0, None)

    assert outcome["result"]["passed"] is False
    assert outcome["result"]["error"] == "simulated worker failure"


def test_run_one_isolated_emits_unit_level_span(monkeypatch):
    """Each unit must carry its own OTel span with task/mode/rep and the
    outcome fields an operator needs to triage a sweep -- otherwise a
    sweep's trace is just an undifferentiated wall of gemini_api_call
    spans with no unit-level view (book-refactor spec Phase B3)."""
    import self_governance.benchmark as bm

    captured_attrs = {}

    class FakeSpan:
        def set_attribute(self, key, value):
            captured_attrs[key] = value

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class FakeTracer:
        def start_as_current_span(self, name):
            captured_attrs["_span_name"] = name
            return FakeSpan()

    monkeypatch.setattr("self_governance.tracing.tracer", FakeTracer())
    monkeypatch.setattr(
        bm,
        "run_baseline_mode",
        lambda task, api_key, model=None: {
            "passed": True,
            "latency_sec": 1.5,
            "estimated_cost_usd": 0.0001,
        },
    )

    bm._run_one_isolated(_TINY_TASK, "baseline", 3, None)

    assert captured_attrs["_span_name"] == "benchmark_unit"
    assert captured_attrs["task_id"] == "task_tiny"
    assert captured_attrs["mode"] == "baseline"
    assert captured_attrs["rep"] == 3
    assert captured_attrs["passed"] is True
    assert captured_attrs["latency_sec"] == 1.5
    assert captured_attrs["estimated_cost_usd"] == 0.0001


def test_cli_benchmark_parallel(monkeypatch, capsys):
    """The --reps > 1 CLI path is a genuinely different code path from the
    default (routes to run_benchmark_parallel, not run_benchmark) and needs
    its own coverage, not just the library-level test."""
    import sys as _sys

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(
        "self_governance.benchmark.load_benchmark_tasks", lambda source=None: [_TINY_TASK]
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
        "self_governance.benchmark.load_benchmark_tasks", lambda source=None: [_TINY_TASK]
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
        "self_governance.benchmark.load_benchmark_tasks", lambda source=None: [_TINY_TASK]
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
         mock.patch.object(bm, "load_benchmark_tasks", lambda source=None: [_TINY_TASK]), \
         mock.patch.object(bm, "as_completed", lambda futures: futures):
        bm.run_benchmark_parallel(api_key=None, reps=1, workers=999)

    assert called["max_workers"] == 16


def test_golden_regression_checker_detects_regressions(tmp_path):
    """The golden-baseline checker must pass the real phase-G data
    against its own thresholds and fail a synthetically regressed
    dataset -- this is the harness-level golden-trajectory test
    (book-refactor spec Phase B1/B2)."""
    import sys as _sys

    _sys.path.insert(0, "telemetry")
    from check_regression import check

    clean = check(
        "telemetry/phase_g_lru_retry_threadsafe_100rep.jsonl",
        "telemetry/golden/phase_g_baseline.json",
    )
    assert clean == [], f"real data regressed against its own goldens: {clean}"

    bad = tmp_path / "bad.jsonl"
    rows = [
        {
            "task_id": "task_lru_cache",
            "mode": "asg",
            "rep": i,
            "result": {
                "passed": i < 10,
                "latency_sec": 200.0,
                "estimated_cost_usd": 0.005,
            },
        }
        for i in range(20)
    ]
    bad.write_text("\n".join(json.dumps(r) for r in rows))
    found = check(str(bad), "telemetry/golden/phase_g_baseline.json")
    assert len(found) == 3, f"expected pass/latency/cost regressions, got: {found}"


def test_load_benchmark_tasks_alternate_source():
    """load_benchmark_tasks(source=...) must load the held-out tier
    (overfitting control, post-validation improvement plan Phase 3.2)
    instead of the packaged suite, and every task must be schema-valid."""
    tasks = load_benchmark_tasks(
        source="src/self_governance/benchmark_tasks_heldout.json"
    )
    assert len(tasks) == 4
    ids = {t["id"] for t in tasks}
    assert ids == {
        "task_rate_limiter",
        "task_priority_queue",
        "task_debounce",
        "task_graph_cycle",
    }
    for t in tasks:
        assert {"id", "name", "description", "test_code", "target_file"} <= set(t)
    # Disjoint from the original suite -- a held-out tier that shares
    # task IDs with the training suite isn't held out.
    assert ids.isdisjoint({t["id"] for t in load_benchmark_tasks()})


def test_run_benchmark_parallel_task_source_param(monkeypatch, tmp_path):
    """task_source must reach load_benchmark_tasks, not just exist as
    a CLI flag with no effect."""
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    captured = {}

    def fake_load(source=None):
        captured["source"] = source
        return [_TASK_A]

    monkeypatch.setattr("self_governance.benchmark.load_benchmark_tasks", fake_load)
    run_benchmark_parallel(
        api_key=None, reps=1, workers=1, task_source="some/heldout.json"
    )
    assert captured["source"] == "some/heldout.json"
