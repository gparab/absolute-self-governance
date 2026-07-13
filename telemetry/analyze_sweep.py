"""Analyze a benchmark checkpoint JSONL: per-task pass rates with Wilson
confidence intervals, latency stats, attempt distribution, and a
plain-language per-task verdict.

Usage:
    python telemetry/analyze_sweep.py <checkpoint.jsonl>

Error rows (quota failures etc.) are excluded -- they are retry
bookkeeping, not results. Never merge checkpoints across providers,
models, or code versions; one file = one experiment.
"""

import json
import math
import sys
from collections import Counter, defaultdict
from statistics import mean, stdev


def wilson_interval(passed: int, n: int, z: float = 1.96):
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (0.0, 0.0)
    p = passed / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = (z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def analyze(path: str) -> None:
    rows = [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]
    total = len(rows)
    rows = [r for r in rows if "error" not in r["result"]]
    print(f"{path}: {len(rows)} result rows ({total - len(rows)} error rows excluded)\n")

    agg: dict = defaultdict(lambda: {"pass": 0, "n": 0, "lat": []})
    for r in rows:
        k = (r["task_id"], r["mode"])
        res = r["result"]
        agg[k]["n"] += 1
        agg[k]["pass"] += 1 if res.get("passed") else 0
        agg[k]["lat"].append(res.get("latency_sec", 0))

    print(f"{'task':<24}{'mode':<10}{'pass':<10}{'rate':<8}{'95% CI':<18}{'meanLat':<9}{'latSD'}")
    for (t, m), v in sorted(agg.items()):
        lo, hi = wilson_interval(v["pass"], v["n"])
        sd = stdev(v["lat"]) if len(v["lat"]) > 1 else 0.0
        print(
            f"{t:<24}{m:<10}{v['pass']}/{v['n']:<8}"
            f"{v['pass'] / v['n']:<8.1%}[{lo:.1%}, {hi:.1%}]   "
            f"{mean(v['lat']):<9.1f}{sd:.1f}"
        )

    print()
    per_mode = {}
    for mode in ("baseline", "asg"):
        m = [r["result"] for r in rows if r["mode"] == mode]
        if not m:
            continue
        passed = sum(1 for x in m if x.get("passed"))
        lat = [x.get("latency_sec", 0) for x in m]
        lo, hi = wilson_interval(passed, len(m))
        per_mode[mode] = (passed, len(m), mean(lat))
        print(
            f"{mode.upper()}: {passed}/{len(m)} ({passed / len(m):.1%}, "
            f"95% CI [{lo:.1%}, {hi:.1%}]), mean latency {mean(lat):.1f}s"
        )

    attempts = Counter(
        r["result"].get("attempts")
        for r in rows
        if r["mode"] == "asg" and r["result"].get("attempts") is not None
    )
    if attempts:
        print(f"ASG attempts distribution: {dict(sorted(attempts.items()))}")

    if "baseline" in per_mode and "asg" in per_mode:
        bp, bn, bl = per_mode["baseline"]
        ap, an, al = per_mode["asg"]
        delta = ap / an - bp / bn
        print(
            f"\nVerdict: ASG {'beats' if delta > 0 else 'ties' if delta == 0 else 'trails'} "
            f"baseline by {delta:+.1%} at {al / bl:.1f}x latency."
            " Whether the delta is meaningful depends on the CIs above overlapping."
        )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    analyze(sys.argv[1])
