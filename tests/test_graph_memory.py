from unittest.mock import patch

import pytest
from self_governance.db import Base, engine, SessionLocal, GraphNode, GraphEdge
from self_governance.graph_memory import FLAW_CATEGORIES, GraphMemoryEngine

@pytest.fixture(scope="function", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

def test_graph_memory_records_session_and_queries():
    tenant = "test_tenant_graph"
    engine = GraphMemoryEngine(tenant_id=tenant)
    
    # Act
    engine.add_session_node(
        session_id=999,
        roster=["Backend Wizard", "QA Specialist"],
        features=["Feature_0", "Feature_1"],
        constraints=["Do not use globals"]
    )
    
    # Assert nodes
    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id=tenant).all()
    # 1 session + 2 roles + 2 features + 1 constraint = 6 nodes
    assert len(nodes) == 6
    
    edges = db.query(GraphEdge).filter_by(tenant_id=tenant).all()
    # 2 roles + 2 features + 1 constraint = 5 edges
    assert len(edges) == 5
    db.close()
    
    # Act query
    context = engine.query_context(["Feature_0"])
    
    # Assert query
    assert "GraphRAG Context:" in context
    assert "Do not use globals" in context


def test_query_context_returns_default_message_when_no_match():
    engine = GraphMemoryEngine(tenant_id="test_tenant_graph_empty")

    context = engine.query_context(["Feature_never_built"])

    assert context == "No specific past graph context found for these features."


def test_add_session_node_rolls_back_and_reraises_on_db_error():
    engine = GraphMemoryEngine(tenant_id="test_tenant_graph_error")

    with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("db exploded")):
        with pytest.raises(RuntimeError, match="db exploded"):
            engine.add_session_node(
                session_id=1,
                roster=["Backend Wizard"],
                features=["Feature_0"],
                constraints=[],
            )

    # No orphaned rows survive the rolled-back transaction.
    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id="test_tenant_graph_error").all()
    db.close()
    assert nodes == []


def test_related_constraints_are_linked_and_surfaced_across_features():
    """A-MEM-style linking (Phase C2b): a constraint filed under one feature
    should surface when querying a *different* feature, if a later
    constraint on that feature shares enough vocabulary to be about the
    same concern."""
    tenant = "test_tenant_graph_amem"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_0"],
        constraints=["Retry network calls with exponential backoff"],
    )
    engine.add_session_node(
        session_id=2, roster=["Backend Wizard"], features=["Feature_1"],
        constraints=["Network calls need exponential backoff on retry"],
    )

    context = engine.query_context(["Feature_1"])

    assert "Network calls need exponential backoff on retry" in context
    assert "Related past constraint" in context
    assert "Retry network calls with exponential backoff" in context


def test_constraint_with_only_stopwords_is_skipped_for_linking_without_crashing():
    tenant = "test_tenant_graph_amem_stopwords"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_0"],
        constraints=["the a of"],
    )
    engine.add_session_node(
        session_id=2, roster=["Backend Wizard"], features=["Feature_1"],
        constraints=["Retry network calls with exponential backoff"],
    )

    context = engine.query_context(["Feature_1"])

    assert "Retry network calls with exponential backoff" in context
    assert "Related past constraint" not in context


def test_related_constraint_seen_via_two_paths_is_not_duplicated():
    """Querying two features whose constraints are mutually linked should not
    print the same related-constraint line twice."""
    tenant = "test_tenant_graph_amem_dedup"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_0"],
        constraints=["Retry network calls with exponential backoff"],
    )
    engine.add_session_node(
        session_id=2, roster=["Backend Wizard"], features=["Feature_1"],
        constraints=["Network calls need exponential backoff on retry"],
    )

    context = engine.query_context(["Feature_0", "Feature_1"])

    assert context.count("Related past constraint") == 1


def test_unrelated_constraints_are_not_linked():
    tenant = "test_tenant_graph_amem_unrelated"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.add_session_node(
        session_id=1, roster=["Backend Wizard"], features=["Feature_0"],
        constraints=["Use UTC timestamps everywhere"],
    )
    engine.add_session_node(
        session_id=2, roster=["QA Specialist"], features=["Feature_1"],
        constraints=["Sanitize HTML before rendering"],
    )

    context = engine.query_context(["Feature_1"])

    assert "Sanitize HTML before rendering" in context
    assert "Related past constraint" not in context
    assert "UTC timestamps" not in context


def test_recommend_procedure_returns_none_for_all_stopword_query():
    tenant = "test_tenant_procedure_stopword_query"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    assert engine.recommend_procedure("the a of") is None


def test_recommend_procedure_skips_candidate_with_all_stopword_trigger():
    tenant = "test_tenant_procedure_stopword_candidate"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="degenerate_strategy", trigger_pattern="the a of", steps=["a"], passed=True,
    )

    assert engine.recommend_procedure("boundary condition test failure") is None


def test_recommend_procedure_returns_none_when_nothing_recorded():
    engine = GraphMemoryEngine(tenant_id="test_tenant_procedure_empty")

    assert engine.recommend_procedure("boundary condition test failure") is None


def test_record_and_recommend_procedure_happy_path():
    tenant = "test_tenant_procedure_basic"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="qa_specialist_first",
        trigger_pattern="boundary condition test failure off by one",
        steps=["Lead with QA Specialist", "Reuse the failing test as the spec"],
        passed=True,
    )

    result = engine.recommend_procedure("boundary condition off by one failure")

    assert result is not None
    assert result["name"] == "qa_specialist_first"
    assert result["success_count"] == 1
    assert result["failure_count"] == 0
    assert result["success_rate"] == 1.0
    assert result["steps"] == ["Lead with QA Specialist", "Reuse the failing test as the spec"]


def test_record_procedure_outcome_accumulates_on_the_same_named_node():
    tenant = "test_tenant_procedure_accumulate"
    engine = GraphMemoryEngine(tenant_id=tenant)

    for _ in range(3):
        engine.record_procedure_outcome(
            name="qa_specialist_first", trigger_pattern="boundary condition test failure",
            steps=["Lead with QA Specialist"], passed=True,
        )
    engine.record_procedure_outcome(
        name="qa_specialist_first", trigger_pattern="boundary condition test failure",
        steps=["Lead with QA Specialist"], passed=False,
    )

    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id=tenant, type="Procedure").all()
    db.close()

    assert len(nodes) == 1
    result = engine.recommend_procedure("boundary condition test failure")
    assert result["success_count"] == 3
    assert result["failure_count"] == 1
    assert result["success_rate"] == 0.75


def test_recommend_procedure_picks_the_higher_success_rate_match():
    tenant = "test_tenant_procedure_pick_best"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy_weak", trigger_pattern="boundary condition test failure",
        steps=["a"], passed=False,
    )
    engine.record_procedure_outcome(
        name="strategy_strong", trigger_pattern="boundary condition test failure",
        steps=["b"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["name"] == "strategy_strong"


def test_recommend_procedure_ignores_dissimilar_trigger_patterns():
    tenant = "test_tenant_procedure_dissimilar"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="unrelated_strategy", trigger_pattern="database connection timeout",
        steps=["a"], passed=True,
    )

    assert engine.recommend_procedure("boundary condition off by one failure") is None


def test_recommend_procedure_ignores_procedures_with_zero_attempts():
    """A procedure with no accumulated outcomes yet shouldn't be recommendable
    -- there's no evidence it works, even if the trigger pattern matches."""
    tenant = "test_tenant_procedure_zero_attempts"
    engine = GraphMemoryEngine(tenant_id=tenant)
    db = SessionLocal()
    import json as _json
    from self_governance.db import GraphNode as _GraphNode
    db.merge(_GraphNode(
        id=f"procedure_{tenant}_untested",
        tenant_id=tenant,
        type="Procedure",
        properties=_json.dumps({
            "name": "untested", "trigger_pattern": "boundary condition test failure",
            "steps": [], "success_count": 0, "failure_count": 0,
        }),
    ))
    db.commit()
    db.close()

    assert engine.recommend_procedure("boundary condition test failure") is None


def test_record_procedure_outcome_rolls_back_and_reraises_on_db_error():
    engine = GraphMemoryEngine(tenant_id="test_tenant_procedure_error")

    with patch("sqlalchemy.orm.Session.commit", side_effect=RuntimeError("db exploded")):
        with pytest.raises(RuntimeError, match="db exploded"):
            engine.record_procedure_outcome(
                name="broken", trigger_pattern="x", steps=[], passed=True
            )

    db = SessionLocal()
    nodes = db.query(GraphNode).filter_by(tenant_id="test_tenant_procedure_error").all()
    db.close()
    assert nodes == []


def test_record_procedure_outcome_tracks_flaw_category_counts():
    tenant = "test_tenant_procedure_flaw_categories"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, flaw_category="tests_failed",
    )
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=False, flaw_category="tests_failed",
    )
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, flaw_category="wrong_persona_order",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["flaw_category_counts"] == {"tests_failed": 2, "wrong_persona_order": 1}


def test_record_procedure_outcome_normalizes_unrecognized_flaw_category_to_unknown():
    tenant = "test_tenant_procedure_flaw_unknown"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, flaw_category="made_up_category_not_in_taxonomy",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["flaw_category_counts"] == {"unknown": 1}


def test_record_procedure_outcome_defaults_flaw_category_to_unknown_when_omitted():
    tenant = "test_tenant_procedure_flaw_omitted"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["flaw_category_counts"] == {"unknown": 1}


def test_record_procedure_outcome_stores_critique_and_caps_at_five():
    tenant = "test_tenant_procedure_critiques"
    engine = GraphMemoryEngine(tenant_id=tenant)

    for i in range(7):
        engine.record_procedure_outcome(
            name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
            passed=True, critique=f"critique {i}",
        )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["critiques"] == [f"critique {i}" for i in range(2, 7)]


def test_record_procedure_outcome_without_critique_leaves_critiques_empty():
    tenant = "test_tenant_procedure_no_critique"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["critiques"] == []


def test_ema_success_score_weights_recent_outcomes_more_than_old_ones():
    tenant = "test_tenant_procedure_ema"
    engine = GraphMemoryEngine(tenant_id=tenant)

    # 3 failures then 1 success: raw success_rate is low (0.25), but the EMA
    # should reflect that the most recent outcome was a success.
    for _ in range(3):
        engine.record_procedure_outcome(
            name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=False,
        )
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["success_rate"] == 0.25
    assert result["ema_success_score"] > result["success_rate"]


def test_recommend_procedure_ranks_by_ema_not_just_success_rate():
    """A strategy with fewer but more recent successes should be able to
    outrank one with a higher raw success_rate but a stale/declining trend,
    since ranking uses the EMA score."""
    tenant = "test_tenant_procedure_ema_ranking"
    engine = GraphMemoryEngine(tenant_id=tenant)

    # strategy_stale: 4 successes then 4 failures -- high raw rate (0.5) but
    # recently failing.
    for _ in range(4):
        engine.record_procedure_outcome(
            name="strategy_stale", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
        )
    for _ in range(4):
        engine.record_procedure_outcome(
            name="strategy_stale", trigger_pattern="boundary condition test failure", steps=["a"], passed=False,
        )
    # strategy_recent: 1 success only.
    engine.record_procedure_outcome(
        name="strategy_recent", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["name"] == "strategy_recent"


def test_recommend_procedure_filters_by_flaw_category():
    tenant = "test_tenant_procedure_filter_flaw"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy_a", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, flaw_category="tests_failed",
    )
    engine.record_procedure_outcome(
        name="strategy_b", trigger_pattern="boundary condition test failure", steps=["b"],
        passed=True, flaw_category="wrong_persona_order",
    )

    result = engine.recommend_procedure("boundary condition test failure", flaw_category="wrong_persona_order")

    assert result["name"] == "strategy_b"


def test_recommend_procedure_returns_none_when_flaw_category_has_no_match():
    tenant = "test_tenant_procedure_filter_flaw_none"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy_a", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, flaw_category="tests_failed",
    )

    result = engine.recommend_procedure("boundary condition test failure", flaw_category="sandbox_error")

    assert result is None


def test_recommend_procedure_falls_back_to_success_rate_when_ema_field_absent():
    """A procedure node written before this field existed (or by any other
    caller bypassing record_procedure_outcome) should still be rankable."""
    tenant = "test_tenant_procedure_no_ema_field"
    db = SessionLocal()
    import json as _json
    from self_governance.db import GraphNode as _GraphNode
    db.merge(_GraphNode(
        id=f"procedure_{tenant}_legacy",
        tenant_id=tenant,
        type="Procedure",
        properties=_json.dumps({
            "name": "legacy", "trigger_pattern": "boundary condition test failure",
            "steps": [], "success_count": 3, "failure_count": 1,
        }),
    ))
    db.commit()
    db.close()

    engine = GraphMemoryEngine(tenant_id=tenant)
    result = engine.recommend_procedure("boundary condition test failure")

    assert result["name"] == "legacy"
    assert result["ema_success_score"] == 0.75


def test_flaw_categories_is_a_fixed_taxonomy_containing_expected_values():
    assert FLAW_CATEGORIES == {
        "tests_failed", "no_files_written", "sandbox_error",
        "wrong_persona_order", "missing_requirement", "ambiguous_requirement",
        "unknown",
    }
