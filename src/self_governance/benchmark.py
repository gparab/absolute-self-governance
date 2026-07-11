import os
import json
import tempfile
import time
import logging
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Callable, List, Dict, Any, Optional
from self_governance.models import Agent

logger = logging.getLogger("self_governance.benchmark")


def load_benchmark_tasks() -> List[Dict[str, Any]]:
    """Loads benchmark challenges from the JSON config."""
    tasks_path = os.path.join(os.path.dirname(__file__), "benchmark_tasks.json")
    with open(tasks_path, "r", encoding="utf-8") as f:
        return json.load(f)


def run_benchmark(api_key: Optional[str] = None, out_path: Optional[str] = None) -> Dict[str, Any]:
    """Runs the diagnostic code challenges under baseline and ASG modes."""
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
            baseline_metrics = run_baseline_mode(task, api_key)
            if checkpoint_f:
                checkpoint_f.write(json.dumps({
                    "task_id": task_id, "mode": "baseline", "rep": 0, "result": baseline_metrics
                }) + "\n")
                checkpoint_f.flush()
        else:
            baseline_metrics = {"passed": False, "latency_sec": 0.0, "estimated_cost_usd": 0.0, "skipped": True}

        # 2. Run ASG (Deliberation, Entropy Sizing, Multi-Agent Loop)
        if (task_id, "asg") not in done_keys:
            asg_metrics = run_asg_mode(task, api_key)
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


def run_baseline_mode(task: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Simulates a baseline run with direct, single-step generation."""
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    from self_governance.metrics import ASG_PIPELINE_LATENCY

    start_time = time.time()
    adapter = GeminiExecutionAdapter(api_key=api_key)

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
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6),
    }


def run_asg_mode(task: Dict[str, Any], api_key: Optional[str]) -> Dict[str, Any]:
    """Simulates the ASG run with consensus deliberation, swarm sizing, and multi-agent pipeline."""
    from self_governance.consensus import run_consensus
    from self_governance.dimensioning import dimension_swarm
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    from self_governance.metrics import ASG_PIPELINE_LATENCY

    start_time = time.time()

    with ASG_PIPELINE_LATENCY.labels(phase="asg").time():
        # Deliberate candidate selection
        consensus_res = run_consensus(
            initial_roster=["agent_dev", "agent_tester", "agent_security"],
            initial_temp=1.0,
            target_tau=8.0,
        )

        # Dynamic swarm sizing using Shannon entropy sizing rules
        req_vector = [0.8, 0.5, 0.7, 0.4]
        matrix = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        _ = dimension_swarm(req_vector, matrix)

        from self_governance.agency_agents_adapter import get_persona

        adapter = GeminiExecutionAdapter(api_key=api_key)

        # Convert consensus results into Agent schemas using real personas
        agents = []
        for r in consensus_res.approved_roster:
            persona = get_persona(r, adapter=adapter)
            agents.append(
                Agent(
                    role=r, 
                    prompt=persona.get("prompt", f"Guide: {r}"), 
                    capabilities=persona.get("capabilities", [])
                )
            )

        # Execute through hardened adapter
        plan = {"task": task["description"]}
        exec_res = adapter.execute_development(agents, plan)
        written_files = exec_res.get("written_files", [])

    # Create test file on disk
    test_filepath = f"bench_test_{task['target_file']}"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(task["test_code"])

    # Run linter and security scan checks
    adapter.review_code(agents, exec_res)
    adapter.run_security_scan(agents, exec_res)

    # Run test verification sandbox
    test_res = adapter.execute_tests(agents, {}, test_target=test_filepath)
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
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6),
    }


def _run_one_isolated(
    task: Dict[str, Any], mode: str, rep: int, api_key: Optional[str]
) -> Dict[str, Any]:
    """Run a single (task, mode, rep) unit in its own process and its own
    tempdir. Runs in a ProcessPoolExecutor worker -- must be a top-level,
    picklable function, and cwd isolation is why this is process-based
    rather than thread-based: os.chdir() is process-global in Python, so
    threads sharing one process cannot each have their own working
    directory. Without this, concurrent reps of the same task would race
    on the same target_file / bench_test_*.py filenames.
    """
    workdir = tempfile.mkdtemp(prefix="asg_bench_")
    prev_cwd = os.getcwd()
    try:
        os.chdir(workdir)
        mode_fn = run_baseline_mode if mode == "baseline" else run_asg_mode
        try:
            result = mode_fn(task, api_key)
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
    """
    workers = max(1, min(workers, 16))
    tasks = load_benchmark_tasks()

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
                pool.submit(_run_one_isolated, task, mode, rep, api_key)
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
                if "error" in outcome["result"]:
                    consecutive_errors += 1
                    if consecutive_errors >= workers * 2:
                        for f in futures:
                            f.cancel()
                        break
                else:
                    consecutive_errors = 0
    finally:
        if checkpoint_f is not None:
            checkpoint_f.close()

    return results
