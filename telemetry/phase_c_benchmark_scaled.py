"""Phase C: scaled baseline-vs-ASG benchmark (5 reps x 6 tasks).

Requires GEMINI_API_KEY. Run from an empty scratch directory -- both
modes write generated code and test fixtures into the process cwd.
Emits incremental JSON to phase_c_benchmark_scaled_results.json in cwd
and a final aggregate summary to stdout.

This exists to check whether the original 3-rep, 2-task pilot
(phase_b_benchmark_results.json) generalizes. It did not: see the
paper's Table 6 and surrounding discussion (Section 4.7) for the honest
result -- ASG mode helped on 2/6 tasks, hurt on 2/6 (including the
trivial one), and had a lower aggregate pass rate than the baseline
while costing ~3.6x more.
"""

import json
import time

from self_governance.benchmark import (
    load_benchmark_tasks,
    run_baseline_mode,
    run_asg_mode,
)

REPS = 5
OUT_PATH = "phase_c_benchmark_scaled_results.json"

tasks = load_benchmark_tasks()
results = {}

start_all = time.time()
for task in tasks:
    task_id = task["id"]
    results[task_id] = {"name": task["name"], "baseline": [], "asg": []}
    for mode_name, mode_fn in (("baseline", run_baseline_mode), ("asg", run_asg_mode)):
        for rep in range(REPS):
            t0 = time.time()
            try:
                r = mode_fn(task, None)
            except Exception as e:
                r = {"passed": False, "latency_sec": 0, "estimated_cost_usd": 0, "error": str(e)}
            results[task_id][mode_name].append(r)
            print(
                f"[{time.time()-start_all:6.0f}s] {task_id} {mode_name} rep {rep+1}/{REPS}: "
                f"{'PASS' if r.get('passed') else 'FAIL'} ({time.time()-t0:.1f}s)"
            )
            json.dump(results, open(OUT_PATH, "w"), indent=2)

print(f"\nDONE in {time.time()-start_all:.0f}s total")

# Aggregate summary
print(f"\n{'Task':<22} {'Mode':<9} {'Pass':<8} {'MeanLat':<9} {'MeanCost':<10}")
for task_id, data in results.items():
    for mode in ("baseline", "asg"):
        runs = data[mode]
        n = len(runs)
        passed = sum(1 for r in runs if r.get("passed"))
        mean_lat = sum(r.get("latency_sec", 0) for r in runs) / n
        mean_cost = sum(r.get("estimated_cost_usd", 0) for r in runs) / n
        print(f"{task_id:<22} {mode:<9} {passed}/{n:<6} {mean_lat:<9.1f} ${mean_cost:<9.6f}")
