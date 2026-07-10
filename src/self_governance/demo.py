"""Zero-setup, zero-cost demo of dynamic swarm sizing.

Uses dimension_swarm's real math (no LLM calls, no API key) to show a
concrete, printable contrast: a trivial task staffs one agent, a complex
one staffs a much larger team. A lightweight mock consensus vote runs
alongside each scenario so the live dashboard's iteration counter moves
in real time if it's open.
"""

import os
import time
from typing import Any, Dict, List

from self_governance.consensus import run_consensus
from self_governance.dimensioning import dimension_swarm

# Demo-only matrix, tuned for a clear before/after story rather than
# mirroring the production webhook_matrix (which optimizes for realistic
# staffing shape, not visual contrast). Dim 0 = "scope", dim 1 = "risk".
DEMO_MATRIX = [
    [0.6, 0.0],   # Backend Wizard: scales with scope
    [0.05, 0.25],  # QA Specialist: mostly scales with risk, a little with scope
    [0.0, 0.35],  # Security Auditor: scales with risk only
]

SCENARIOS = [
    {
        "label": "Trivial task",
        "description": "Fix a broken link in the README",
        "requirement_vector": [1.0, 0.0],
    },
    {
        "label": "Complex task",
        "description": "Add multi-tenant billing with per-tenant rate limiting and audit logging",
        "requirement_vector": [3.0, 4.0],
    },
]


def run_scenario(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Compute swarm sizing for one scenario and run a free mock consensus
    vote over the resulting roster (moves the dashboard's iteration count)."""
    swarm_config = dimension_swarm(scenario["requirement_vector"], DEMO_MATRIX)
    roles = [agent.role for agent in swarm_config.swarm]
    role_counts: Dict[str, int] = {}
    for role in roles:
        role_counts[role] = role_counts.get(role, 0) + 1

    approved: List[str] = []
    if roles:
        # run_consensus auto-detects GEMINI_API_KEY from the environment and
        # would switch to a real, paid adapter if it's set — breaking this
        # command's "zero cost, no API key required" promise. Force the
        # mock path regardless of what's in the ambient environment; only
        # this process is affected, restored immediately after.
        real_key = os.environ.pop("GEMINI_API_KEY", None)
        try:
            result = run_consensus(list(dict.fromkeys(roles)), seed=42)
        finally:
            if real_key is not None:
                os.environ["GEMINI_API_KEY"] = real_key
        approved = result.approved_roster

    return {
        "label": scenario["label"],
        "description": scenario["description"],
        "team_size": len(roles),
        "role_counts": role_counts,
        "approved_roster": approved,
    }


def print_scenario_result(result: Dict[str, Any]) -> None:
    print(f"\n  {result['label']}: \"{result['description']}\"")
    if result["team_size"] == 0:
        print("    -> 0 agents staffed")
        return
    breakdown = ", ".join(
        f"{count}x {role}" for role, count in result["role_counts"].items()
    )
    print(f"    -> {result['team_size']} agent(s) staffed: {breakdown}")
    print(f"    -> approved by consensus vote: {', '.join(result['approved_roster'])}")


def print_summary(results: List[Dict[str, Any]]) -> None:
    print("\n" + "=" * 60)
    print("  Summary: team size scales with task complexity")
    print("=" * 60)
    for result in results:
        print(f"  {result['label']:<14} {result['team_size']} agent(s)")
    print()
    print("  No API key was used. No cost was incurred. This is what")
    print("  ASG computes before any LLM call happens.")
    print("=" * 60)


def run_demo(pause_seconds: float = 3.0) -> List[Dict[str, Any]]:
    """Run all demo scenarios in sequence with a pause between them so a
    live dashboard (if open) visibly updates between the two."""
    results = []
    for i, scenario in enumerate(SCENARIOS):
        if i > 0:
            print(f"\n  (pausing {int(pause_seconds)}s -- now watch a harder task...)")
            time.sleep(pause_seconds)
        result = run_scenario(scenario)
        print_scenario_result(result)
        results.append(result)
    print_summary(results)
    return results
