"""Golden-baseline regression check for benchmark sweeps.

Compares a checkpoint JSONL against the committed golden thresholds
(derived from a real validated sweep) and fails loudly on regression:
pass rate below floor, latency p95 above ceiling, or mean cost above
ceiling, per (task, mode) cell. This is the harness-level version of
golden-trajectory regression testing (Roitman 2026, §25.5.3): model
output text varies run to run, so the pinned invariants are the
structural ones -- did it pass as often, as fast, and as cheaply.

Usage:
    python telemetry/check_regression.py <checkpoint.jsonl> [golden.json]

Exit code 0 = no regression; 1 = regression detected. Cells present in
the checkpoint but absent from the golden file are reported as
uncovered, not failed -- new tasks aren't regressions.
"""

import json
import sys
from collections import defaultdict
from statistics import mean

DEFAULT_GOLDEN = "telemetry/golden/phase_g_baseline.json"


def load_cells(checkpoint_path: str) -> dict:
    rows = [
        json.loads(line)
        for line in open(checkpoint_path, encoding="utf-8")
        if line.strip()
    ]
    rows = [r for r in rows if "error" not in r["result"]]
    agg: dict = defaultdict(lambda: {"pass": 0, "n": 0, "lat": [], "cost": []})
    for r in rows:
        cell = agg[f"{r['task_id']}/{r['mode']}"]
        res = r["result"]
        cell["n"] += 1
        cell["pass"] += 1 if res.get("passed") else 0
        cell["lat"].append(res.get("latency_sec", 0))
        cell["cost"].append(res.get("estimated_cost_usd", 0))
    return agg


def check(checkpoint_path: str, golden_path: str = DEFAULT_GOLDEN) -> list:
    """Returns a list of regression strings; empty means clean."""
    golden = json.load(open(golden_path, encoding="utf-8"))["cells"]
    cells = load_cells(checkpoint_path)
    regressions = []
    for key, cell in sorted(cells.items()):
        if key not in golden:
            print(f"  (uncovered by golden baseline: {key} -- skipped)")
            continue
        g = golden[key]
        rate = cell["pass"] / cell["n"]
        lat_sorted = sorted(cell["lat"])
        p95 = lat_sorted[int(0.95 * len(lat_sorted))] if lat_sorted else 0.0
        avg_cost = mean(cell["cost"]) if cell["cost"] else 0.0
        if rate < g["pass_rate_floor"]:
            regressions.append(
                f"{key}: pass rate {rate:.1%} below floor {g['pass_rate_floor']:.1%}"
            )
        if p95 > g["latency_p95_ceiling_sec"]:
            regressions.append(
                f"{key}: latency p95 {p95:.1f}s above ceiling {g['latency_p95_ceiling_sec']}s"
            )
        if avg_cost > g["mean_cost_ceiling_usd"]:
            regressions.append(
                f"{key}: mean cost ${avg_cost:.6f} above ceiling ${g['mean_cost_ceiling_usd']}"
            )
    return regressions


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    golden_arg = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_GOLDEN
    found = check(sys.argv[1], golden_arg)
    if found:
        print("REGRESSIONS DETECTED:")
        for r in found:
            print(f"  - {r}")
        sys.exit(1)
    print("No regressions against golden baseline.")
