import os
import json
import tempfile
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, List, Dict, Any, Optional
from self_governance.models import Agent

logger = logging.getLogger("self_governance.benchmark")


def load_benchmark_tasks(source: Optional[str] = None) -> List[Dict[str, Any]]:
    """Loads benchmark challenges from a JSON config.

    source, if given, is a path to an alternate tasks file -- e.g.
    benchmark_tasks_heldout.json, a task set designed without visibility
    into the ASG mechanism's specifics, used as the control against
    overfitting to the original six tasks the mechanism was iterated
    against (see the post-validation improvement plan, Phase 3.2).
    Defaults to the packaged benchmark_tasks.json.
    """
    tasks_path = source or os.path.join(
        os.path.dirname(__file__), "benchmark_tasks.json"
    )
    with open(tasks_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_benchmark(
    api_key: Optional[str] = None,
    out_path: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Runs the diagnostic code challenges under baseline and ASG modes.

    model, if given, overrides the adapter's configured default for every
    call in the sweep -- baseline and ASG must run against the same model
    to be a valid comparison. Leave unset to use whatever is configured
    via config.yaml/OrchestratorConfig (see docs/BENCHMARKING.md).
    """
    tasks = load_benchmark_tasks()
    results = {}

    # Load previously completed outcomes if resuming
    done_keys = set()
    if out_path:
        for outcome in _load_resume_outcomes(out_path):
            if "error" not in outcome["result"]:
                done_keys.add((outcome["task_id"], outcome["mode"]))

    checkpoint_f = open(out_path, "a", encoding="utf-8") if out_path else None

    for task in tasks:
        task_id = task["id"]
        logger.info("Starting evaluation for benchmark task: %s", task["name"])

        # 1. Run Baseline (Direct Single-Agent Code Gen)
        if (task_id, "baseline") not in done_keys:
            baseline_metrics = run_baseline_mode(task, api_key, model=model)
            if checkpoint_f:
                checkpoint_f.write(json.dumps({
                    "task_id": task_id, "mode": "baseline", "rep": 0, "result": baseline_metrics
                }) + "\n")
                checkpoint_f.flush()
        else:
            baseline_metrics = {"passed": False, "latency_sec": 0.0, "estimated_cost_usd": 0.0, "skipped": True}

        # 2. Run ASG (Deliberation, Entropy Sizing, Multi-Agent Loop)
        if (task_id, "asg") not in done_keys:
            asg_metrics = run_asg_mode(task, api_key, model=model)
            if checkpoint_f:
                checkpoint_f.write(json.dumps({
                    "task_id": task_id, "mode": "asg", "rep": 0, "result": asg_metrics
                }) + "\n")
                checkpoint_f.flush()
        else:
            asg_metrics = {"passed": False, "latency_sec": 0.0, "estimated_cost_usd": 0.0, "skipped": True}

        results[task_id] = {
            "name": task["name"],
            "baseline": baseline_metrics,
            "asg": asg_metrics,
        }

    if checkpoint_f:
        checkpoint_f.close()

    return results


# Failure taxonomy (after Roitman 2026, Table 25.1): a benchmark unit that
# fails is not one kind of event. Three sweeps in this repo's history were
# invalidated by infrastructure failures (revoked API key, Docker daemon
# down, a broken harness shim) that were indistinguishable from genuine
# test failures in the recorded data -- each burned a full day's API quota
# before being caught by a human noticing implausible aggregate numbers.
# Classifying failures at the source makes that class of invalid dataset
# self-announcing instead of silent.
_SANDBOX_ERROR_MARKERS = (
    "Cannot connect to the Docker daemon",
    "Containerized test execution failed",
    "Unable to find image",
    "docker: command not found",
)


def _classify_failure(
    passed: bool, written_files: List[str], test_res: Dict[str, Any]
) -> Optional[str]:
    """Classify a failed unit: 'sandbox_error' (test environment broken --
    result says nothing about code quality), 'no_files_written' (generation
    produced nothing runnable: API failure or unrecoverable format failure),
    or 'tests_failed' (real quality signal: code ran and failed the tests).
    None when the unit passed."""
    if passed:
        return None
    output = str(
        test_res.get("raw_test_output") or test_res.get("output") or ""
    )
    if any(marker in output for marker in _SANDBOX_ERROR_MARKERS):
        return "sandbox_error"
    if not written_files:
        return "no_files_written"
    return "tests_failed"


def run_baseline_mode(
    task: Dict[str, Any], api_key: Optional[str], model: Optional[str] = None
) -> Dict[str, Any]:
    """Simulates a baseline run with direct, single-step generation."""
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    from self_governance.metrics import ASG_PIPELINE_LATENCY

    start_time = time.time()
    # Force every stage onto the same model when one is given: the
    # constructor's model_development/review/security each fall back
    # independently to config.yaml, so model_default alone would not
    # guarantee a single-model run.
    adapter = GeminiExecutionAdapter(
        api_key=api_key,
        model_default=model,
        model_development=model,
        model_review=model,
        model_security=model,
    )

    plan = {"task": task["description"]}

    # Direct code execution
    with ASG_PIPELINE_LATENCY.labels(phase="baseline").time():
        exec_res = adapter.execute_development([], plan)
    written_files = exec_res.get("written_files", [])

    # Create test file on disk
    test_filepath = f"bench_test_{task['target_file']}"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(task["test_code"])

    # Run tests on host
    test_res = adapter.execute_tests([], {}, test_target=test_filepath)
    passed = test_res.get("status") == "completed"

    # Cleanup files
    for f_path in written_files:
        try:
            os.remove(f_path)
        except Exception:  # nosec B110
            pass
    try:
        os.remove(test_filepath)
    except Exception:  # nosec B110
        pass

    latency = time.time() - start_time
    # Use adapter abstraction to compute cost
    billing_metrics = adapter.get_billing_metrics()
    cost = billing_metrics.get("estimated_cost_usd", 0.0)

    return {
        "passed": passed,
        "failure_class": _classify_failure(passed, written_files, test_res),
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6),
    }


def run_asg_mode(
    task: Dict[str, Any], api_key: Optional[str], model: Optional[str] = None
) -> Dict[str, Any]:
    """ASG mode: perspective-rotating, test-verified attempts.

    Up to three attempts per task. Each attempt is led by a different
    specialist persona, sees the acceptance tests (the tests ARE the
    spec), and sees the previous attempt's failure output; the sandbox
    verdict ends the loop on first pass. This merges best-of-N
    perspective diversity, failure-feedback repair, and early exit into
    one loop -- the mechanisms that make a multi-agent pipeline
    measurably better than one-shot generation.

    Deliberately absent from this path (all measured contributing zero
    corrective power across two full sweeps -- see the repair-loop spec):
    the TETD consensus annealing loop (its outcome is constant for this
    fixed 3-role roster; it remains the production mechanism for dynamic
    rosters on the webhook path), dimension_swarm (result was discarded),
    and the review/security stages (outputs were discarded).

    Baseline stays a single description-only attempt by definition. Every
    attempt's full latency and token cost lands in this unit's metrics --
    no free retries.
    """
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    from self_governance.metrics import ASG_PIPELINE_LATENCY
    from self_governance.agency_agents_adapter import get_persona
    from self_governance.fact_extraction import extract_facts

    start_time = time.time()

    adapter = GeminiExecutionAdapter(
        api_key=api_key,
        model_default=model,
        model_development=model,
        model_review=model,
        model_security=model,
    )

    roster = ["Backend Wizard", "QA Specialist", "Security Auditor"]

    # Create test file on disk
    test_filepath = f"bench_test_{task['target_file']}"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(task["test_code"])

    written_files: List[str] = []
    passed = False
    attempts = 0
    failure_log = ""
    # Stall detection (looper's no-progress-signature rule, July 2026 topic-page
    # batch): a rewrite that produces the exact same failing-test set as the
    # previous attempt made no progress, distinct from a rewrite that fails
    # differently. Signature is the parsed set of failing tests (fact_extraction's
    # existing FAILED-line regex), not raw output text, so it's stable across
    # attempts even if timing/whitespace differs. Purely an observability signal
    # here -- not wired into procedural memory or the pass/fail verdict itself.
    prior_failure_signature: Optional[frozenset] = None
    stalled_attempts = 0

    with ASG_PIPELINE_LATENCY.labels(phase="asg").time():
        for role in roster:
            attempts += 1
            persona = get_persona(role)
            agent = Agent(
                role=role,
                prompt=persona.get("prompt", f"Guide: {role}"),
                capabilities=persona.get("capabilities", []),
            )
            plan = {
                "task": task["description"],
                "acceptance_tests": task["test_code"],
                "lead_perspective": role,
                # Disjoint write-scope (Agent-Loop-Skills' pattern, July 2026
                # topic-page batch): the generating persona must not be able
                # to make its own attempt pass by overwriting the acceptance
                # test file it's being judged against.
                "protected_write_paths": [test_filepath],
            }
            if failure_log:
                plan["previous_attempt_failed_tests"] = str(failure_log)[:4000]
                plan["instruction"] = (
                    "A previous attempt failed the acceptance tests above. "
                    "Rewrite the implementation file so the tests pass."
                )

            exec_res = adapter.execute_development([agent], plan)
            for f_path in exec_res.get("written_files", []):
                if f_path not in written_files:
                    written_files.append(f_path)

            test_res = adapter.execute_tests([agent], {}, test_target=test_filepath)
            passed = test_res.get("status") == "completed"
            if passed:
                break
            failure_log = test_res.get("raw_test_output") or test_res.get("output", "")

            current_signature = frozenset(extract_facts(pytest_output=str(failure_log)))
            if current_signature and current_signature == prior_failure_signature:
                stalled_attempts += 1
                logger.warning(
                    "ASG mode: attempt %d for role %s made no progress -- identical failing-test set as the previous attempt.",
                    attempts, role,
                )
            prior_failure_signature = current_signature or prior_failure_signature

    # Cleanup files
    for f_path in written_files:
        try:
            os.remove(f_path)
        except Exception:  # nosec B110
            pass
    try:
        os.remove(test_filepath)
    except Exception:  # nosec B110
        pass

    latency = time.time() - start_time
    # Use adapter abstraction to compute cost
    billing_metrics = adapter.get_billing_metrics()
    cost = billing_metrics.get("estimated_cost_usd", 0.0)

    return {
        "passed": passed,
        "attempts": attempts,
        "stalled_attempts": stalled_attempts,
        "failure_class": _classify_failure(passed, written_files, test_res),
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6),
    }


def _run_one_isolated(
    task: Dict[str, Any],
    mode: str,
    rep: int,
    api_key: Optional[str],
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Run a single (task, mode, rep) unit in its own process and its own
    tempdir. Runs in a ProcessPoolExecutor worker -- must be a top-level,
    picklable function, and cwd isolation is why this is process-based
    rather than thread-based: os.chdir() is process-global in Python, so
    threads sharing one process cannot each have their own working
    directory. Without this, concurrent reps of the same task would race
    on the same target_file / bench_test_*.py filenames.
    """
    from self_governance.tracing import tracer

    workdir = tempfile.mkdtemp(prefix="asg_bench_")
    prev_cwd = os.getcwd()
    # One span per (task, mode, rep) unit, carrying the fields an operator
    # actually needs to triage a sweep: which unit, how it failed, what it
    # cost. Individual API-call spans already exist inside the adapter but
    # give no unit-level view; this is what makes a sweep's OTel trace
    # actually traceable end-to-end instead of a wall of undifferentiated
    # gemini_api_call spans.
    with tracer.start_as_current_span("benchmark_unit") as span:
        span.set_attribute("task_id", task["id"])
        span.set_attribute("mode", mode)
        span.set_attribute("rep", rep)
        try:
            os.chdir(workdir)
            mode_fn = run_baseline_mode if mode == "baseline" else run_asg_mode
            try:
                result = mode_fn(task, api_key, model=model)
            except Exception as e:
                result = {
                    "passed": False,
                    "latency_sec": 0.0,
                    "estimated_cost_usd": 0.0,
                    "error": str(e),
                }
        finally:
            os.chdir(prev_cwd)
            import shutil

            shutil.rmtree(workdir, ignore_errors=True)

        span.set_attribute("passed", bool(result.get("passed")))
        span.set_attribute("failure_class", result.get("failure_class") or "")
        span.set_attribute("attempts", result.get("attempts") or 1)
        span.set_attribute("latency_sec", result.get("latency_sec", 0.0))
        span.set_attribute("estimated_cost_usd", result.get("estimated_cost_usd", 0.0))
        if "error" in result:
            span.set_attribute("error", result["error"])

    return {"task_id": task["id"], "mode": mode, "rep": rep, "result": result}


def _load_resume_outcomes(resume_path: str) -> List[Dict[str, Any]]:
    """Read previously-completed outcomes from a JSONL checkpoint file.
    Missing file means a fresh run, not an error."""
    if not os.path.exists(resume_path):
        return []
    outcomes = []
    with open(resume_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                outcomes.append(json.loads(line))
    return outcomes


def run_benchmark_parallel(
    api_key: Optional[str] = None,
    reps: int = 5,
    workers: int = 4,
    on_result: Optional[Callable[[Dict[str, Any]], None]] = None,
    resume_path: Optional[str] = None,
    model: Optional[str] = None,
    task_ids: Optional[List[str]] = None,
    task_source: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the benchmark across multiple repetitions, concurrently, each
    repetition isolated in its own process and working directory.

    Separate from run_benchmark() deliberately: that function is the
    simple, sequential, in-process default used by `self-governance
    benchmark` and covered by tests that monkeypatch GeminiExecutionAdapter
    at the class level -- a monkeypatch in the parent test process does not
    reach child processes spawned by ProcessPoolExecutor, so this path
    cannot be made a drop-in replacement without breaking that test's
    ability to mock network calls. Use this function when you actually
    want a real, larger sweep against a real API key.

    on_result, if given, is called after each individual (task, mode, rep)
    completes -- e.g. to persist incremental progress to disk, since a
    large sweep can run for a long time and losing all progress on a crash
    partway through is a real, previously-experienced cost, not a
    hypothetical one.

    resume_path, if given, makes the sweep resumable across many short
    runs (e.g. a free-tier daily quota cutting a run off mid-sweep): each
    outcome is appended to this file as JSONL as it completes, and any
    (task_id, mode, rep) already present there is skipped on the next
    call instead of re-run. Outcomes with an "error" (e.g. a 429 that
    outlasted the adapter's own retries) are NOT treated as done -- a
    quota cutoff can fail dozens of queued units in a row, and marking
    those permanently "complete" would silently and irrecoverably drop
    them from every future resume.

    model, if given, overrides the configured default for every call in
    the sweep. A resumed sweep must use the same model as the run that
    started its checkpoint file -- mixing models within one checkpoint
    invalidates the baseline-vs-ASG comparison; use a separate
    resume_path per model.

    task_ids, if given, restricts the sweep to that subset of task ids
    instead of the full suite -- e.g. concentrating reps on the tasks
    that actually show variance between modes, since tasks already at
    30/30 in both modes add spend without adding statistical power.

    task_source, if given, is a path to an alternate tasks JSON file
    (e.g. benchmark_tasks_heldout.json) instead of the packaged suite.
    Use a resume_path specific to that source -- a checkpoint mixing
    task IDs from two different source files is not a coherent sweep.
    """
    workers = max(1, min(workers, 16))
    tasks = load_benchmark_tasks(task_source)
    if task_ids is not None:
        tasks = [t for t in tasks if t["id"] in task_ids]
        missing = set(task_ids) - {t["id"] for t in tasks}
        if missing:
            raise ValueError(f"Unknown task_ids: {sorted(missing)}")

    done_keys = set()
    results: Dict[str, Any] = {
        t["id"]: {"name": t["name"], "baseline": [], "asg": []} for t in tasks
    }
    if resume_path:
        for outcome in _load_resume_outcomes(resume_path):
            if "error" in outcome["result"]:
                continue
            done_keys.add((outcome["task_id"], outcome["mode"], outcome["rep"]))
            results[outcome["task_id"]][outcome["mode"]].append(outcome["result"])

    units = [
        (task, mode, rep)
        for task in tasks
        for mode in ("baseline", "asg")
        for rep in range(reps)
        if (task["id"], mode, rep) not in done_keys
    ]

    checkpoint_f = open(resume_path, "a", encoding="utf-8") if resume_path else None
    consecutive_errors = 0
    try:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_run_one_isolated, task, mode, rep, api_key, model)
                for task, mode, rep in units
            ]
            for future in as_completed(futures):
                outcome = future.result()
                results[outcome["task_id"]][outcome["mode"]].append(outcome["result"])
                if checkpoint_f is not None:
                    checkpoint_f.write(json.dumps(outcome) + "\n")
                    checkpoint_f.flush()
                if on_result is not None:
                    on_result(outcome)
                # Once every worker in flight is failing (e.g. a daily quota
                # exhausted), the rest of the queue is doomed too -- stop
                # burning wall-clock on retries that will only produce more
                # of the same error, and let the next scheduled attempt
                # (after quota resets) pick up the remaining units instead.
                # sandbox_error units count too: a broken test environment
                # can never produce valid data, only quota-burning noise
                # (a Docker-down incident once invalidated 96 units before
                # a human noticed the impossible aggregate numbers).
                infra_failure = (
                    "error" in outcome["result"]
                    or outcome["result"].get("failure_class") == "sandbox_error"
                )
                if infra_failure:
                    consecutive_errors += 1
                    if consecutive_errors >= workers * 2:
                        logger.error(
                            "Aborting sweep: %d consecutive infrastructure "
                            "failures -- environment is broken, further units "
                            "would be invalid.",
                            consecutive_errors,
                        )
                        for f in futures:
                            f.cancel()
                        break
                else:
                    consecutive_errors = 0
    finally:
        if checkpoint_f is not None:
            checkpoint_f.close()

    return results
