"""Production evaluation harness for GraphMemoryEngine recall and tenant isolation.

Phase C2 (book spec, §17.11: Mem0-style fact extraction, A-MEM dynamic
linking) was explicitly recorded as "not built" until two things exist: C1
shipping (done -- nudger.py now writes a reflection constraint after every
verified succession) and a production evaluation harness that can measure
whether the memory read path actually works, so future memory-quality claims
have something to be measured against instead of asserted.

This script seeds a synthetic multi-tenant, multi-session scenario into an
isolated database and checks the properties C1's read/write loop depends on:
recall (a reflection written for a feature is retrievable by a later session
building that feature), tenant isolation (one tenant's constraints never leak
into another's context), and feature isolation (querying an unbuilt feature
returns the documented default, not noise).

Usage:
    python telemetry/eval_memory_recall.py

Exit code 0 = all checks passed; 1 = a check failed. Uses its own temp
SQLite database (TESTING=True) -- never touches production or test data.
"""

import os
import sys

os.environ.setdefault("TESTING", "True")

from self_governance.db import Base, engine  # noqa: E402
from self_governance.graph_memory import GraphMemoryEngine  # noqa: E402


def run_checks() -> list[tuple[str, bool, str]]:
    """Seeds the scenario and returns (check_name, passed, detail) tuples."""
    Base.metadata.create_all(bind=engine)

    tenant_a = GraphMemoryEngine(tenant_id="eval_tenant_a")
    tenant_b = GraphMemoryEngine(tenant_id="eval_tenant_b")

    tenant_a.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_X"],
        constraints=["Use retry backoff for network calls in Feature_X"],
    )
    tenant_a.add_session_node(
        session_id=2, roster=["QA Specialist"], features=["Feature_Y"],
        constraints=["Feature_Y requires an idempotency key"],
    )
    tenant_a.add_session_node(
        session_id=3, roster=["Backend Wizard"], features=["Feature_X"], constraints=[],
    )
    tenant_b.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_X"],
        constraints=["Feature_X must batch under 50 items"],
    )

    checks = []

    ctx = tenant_a.query_context(["Feature_X"])
    checks.append((
        "recall: tenant sees its own past constraint for a rebuilt feature",
        "Use retry backoff for network calls in Feature_X" in ctx,
        ctx,
    ))
    checks.append((
        "isolation: tenant_a never sees tenant_b's constraint text",
        "Feature_X must batch under 50 items" not in ctx,
        ctx,
    ))

    ctx_b = tenant_b.query_context(["Feature_X"])
    checks.append((
        "isolation: tenant_b never sees tenant_a's constraint text",
        "Use retry backoff" not in ctx_b,
        ctx_b,
    ))

    ctx_combined = tenant_a.query_context(["Feature_X", "Feature_Y"])
    checks.append((
        "recall: multi-feature query returns constraints for every matched feature",
        "Use retry backoff for network calls in Feature_X" in ctx_combined
        and "Feature_Y requires an idempotency key" in ctx_combined,
        ctx_combined,
    ))

    ctx_unbuilt = tenant_a.query_context(["Feature_never_built"])
    checks.append((
        "feature isolation: unbuilt feature returns the documented default, not noise",
        ctx_unbuilt == "No specific past graph context found for these features.",
        ctx_unbuilt,
    ))

    return checks


def main() -> int:
    checks = run_checks()
    all_passed = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            all_passed = False
            print(f"       context returned: {detail!r}")

    n_passed = sum(1 for _, p, _ in checks if p)
    print(f"\n{n_passed}/{len(checks)} checks passed.")
    if not all_passed:
        print("Memory recall harness FAILED -- do not build on top of C1 until this is green.")
        return 1
    print("Memory recall harness green. C1's read/write loop is trustworthy enough to extend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
