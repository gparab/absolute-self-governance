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
returns the documented default, not noise). It also checks Phase C2b's A-MEM-
style lexical linking: a constraint on one feature surfaces when querying a
different feature whose constraint shares enough vocabulary to be the same
concern, and stays silent when it doesn't. And Phase D3's procedural
memory: the higher-success-rate strategy is recommended for a lexically
similar failure shape between two competing candidates, and a dissimilar
failure shape gets no recommendation at all.

Usage:
    python telemetry/eval_memory_recall.py

Exit code 0 = all checks passed; 1 = a check failed. Uses its own temp
SQLite database (TESTING=True) -- never touches production or test data.
"""

import os
import sys

os.environ.setdefault("TESTING", "True")

from self_governance.alerts import AlertEngine, default_alert_rules  # noqa: E402
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

    # C2b: A-MEM-style linking across features that share vocabulary.
    tenant_c = GraphMemoryEngine(tenant_id="eval_tenant_c")
    tenant_c.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_M"],
        constraints=["Retry network calls with exponential backoff"],
    )
    tenant_c.add_session_node(
        session_id=2, roster=["Backend Wizard"], features=["Feature_N"],
        constraints=["Network calls need exponential backoff on retry"],
    )
    tenant_c.add_session_node(
        session_id=3, roster=["QA Specialist"], features=["Feature_N"],
        constraints=["Sanitize HTML before rendering"],
    )
    ctx_linked = tenant_c.query_context(["Feature_N"])
    checks.append((
        "C2b linking: a lexically related constraint from a different feature is surfaced",
        "Related past constraint" in ctx_linked and "exponential backoff" in ctx_linked,
        ctx_linked,
    ))
    checks.append((
        "C2b linking: an unrelated constraint on the same feature is not marked as linked noise",
        ctx_linked.count("Related past constraint") == 1,
        ctx_linked,
    ))

    # D3: procedural memory -- recommend the best-known strategy for a
    # failure shape, preferring higher success rate over a mere match.
    tenant_d = GraphMemoryEngine(tenant_id="eval_tenant_d")
    tenant_d.record_procedure_outcome(
        name="strategy_weak", trigger_pattern="boundary condition test failure off by one",
        steps=["Retry with the same persona order"], passed=False,
    )
    tenant_d.record_procedure_outcome(
        name="strategy_strong", trigger_pattern="boundary condition test failure off by one",
        steps=["Lead with QA Specialist", "Reuse the failing test as the spec"], passed=True,
    )
    recommendation = tenant_d.recommend_procedure("off by one boundary failure in a test")
    checks.append((
        "D3 procedural memory: recommends the higher-success-rate strategy for a similar failure shape",
        recommendation is not None and recommendation["name"] == "strategy_strong",
        str(recommendation),
    ))

    unrelated_recommendation = tenant_d.recommend_procedure("database connection pool exhausted")
    checks.append((
        "D3 procedural memory: dissimilar failure shape gets no recommendation",
        unrelated_recommendation is None,
        str(unrelated_recommendation),
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
        # Phase D4: run the failed check names through the same alert engine
        # nudger.py uses, instead of ad hoc printing -- this is the one
        # place that rule is meant to fire from a one-shot script.
        failed_names = [name for name, passed, _ in checks if not passed]
        alert_engine = AlertEngine(rules=default_alert_rules())
        for alert in alert_engine.check({"memory_recall_failed_checks": failed_names}):
            print(f"ALERT [{alert.rule_name}]: {alert.message}")
        print("Memory recall harness FAILED -- do not build on top of C1 until this is green.")
        return 1
    print("Memory recall harness green. C1's read/write loop is trustworthy enough to extend.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
