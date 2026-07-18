from unittest.mock import patch

import pytest
from self_governance.db import Base, engine, SessionLocal, GraphNode, GraphEdge
from self_governance.graph_memory import EVIDENCE_TAGS, FLAW_CATEGORIES, GraphMemoryEngine

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


def test_recommend_procedure_default_epsilon_always_returns_best():
    """epsilon=0.0 (the default) must behave identically to before this
    feature existed -- pure exploitation, no randomness."""
    tenant = "test_tenant_procedure_epsilon_default"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="strategy_weak", trigger_pattern="boundary condition test failure", steps=["a"], passed=False,
    )
    engine.record_procedure_outcome(
        name="strategy_strong", trigger_pattern="boundary condition test failure", steps=["b"], passed=True,
    )

    for _ in range(20):
        result = engine.recommend_procedure("boundary condition test failure")
        assert result["name"] == "strategy_strong"


def test_recommend_procedure_epsilon_one_with_forced_roll_returns_an_alternative():
    import random as _random

    tenant = "test_tenant_procedure_epsilon_explore"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="strategy_weak", trigger_pattern="boundary condition test failure", steps=["a"], passed=False,
    )
    engine.record_procedure_outcome(
        name="strategy_strong", trigger_pattern="boundary condition test failure", steps=["b"], passed=True,
    )

    rng = _random.Random(0)  # first call to .random() with seed 0 is < 1.0, always triggers explore
    result = engine.recommend_procedure("boundary condition test failure", epsilon=1.0, rng=rng)

    assert result["name"] == "strategy_weak"


def test_recommend_procedure_epsilon_has_no_effect_with_a_single_candidate():
    tenant = "test_tenant_procedure_epsilon_single"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="only_strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure", epsilon=1.0)

    assert result["name"] == "only_strategy"


def test_recommend_procedure_epsilon_zero_roll_returns_best_even_with_epsilon_set():
    import random as _random

    tenant = "test_tenant_procedure_epsilon_no_roll"
    engine = GraphMemoryEngine(tenant_id=tenant)
    engine.record_procedure_outcome(
        name="strategy_weak", trigger_pattern="boundary condition test failure", steps=["a"], passed=False,
    )
    engine.record_procedure_outcome(
        name="strategy_strong", trigger_pattern="boundary condition test failure", steps=["b"], passed=True,
    )

    class NeverExplore(_random.Random):
        def random(self):
            return 0.999  # always >= any reasonable epsilon < 1.0

    result = engine.recommend_procedure("boundary condition test failure", epsilon=0.5, rng=NeverExplore())

    assert result["name"] == "strategy_strong"


def test_flaw_categories_is_a_fixed_taxonomy_containing_expected_values():
    assert FLAW_CATEGORIES == {
        "tests_failed", "no_files_written", "sandbox_error",
        "wrong_persona_order", "missing_requirement", "ambiguous_requirement",
        "unknown",
    }


def test_evidence_tags_is_a_fixed_taxonomy_containing_expected_values():
    assert EVIDENCE_TAGS == {"FACT", "INFERENCE", "ASSUMPTION", "UNKNOWN"}


def test_untagged_outcome_gets_full_confidence_unchanged_from_before():
    tenant = "test_tenant_evidence_untagged"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["ema_success_score"] == 1.0


def test_fact_tagged_outcome_gets_full_confidence():
    tenant = "test_tenant_evidence_fact"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, evidence_tag="FACT",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["ema_success_score"] == 1.0


def test_assumption_tagged_outcome_moves_score_less_than_fact():
    tenant_fact = "test_tenant_evidence_fact_cmp"
    tenant_assumption = "test_tenant_evidence_assumption_cmp"
    engine_fact = GraphMemoryEngine(tenant_id=tenant_fact)
    engine_assumption = GraphMemoryEngine(tenant_id=tenant_assumption)

    engine_fact.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, evidence_tag="FACT",
    )
    engine_assumption.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, evidence_tag="ASSUMPTION",
    )

    fact_result = engine_fact.recommend_procedure("boundary condition test failure")
    assumption_result = engine_assumption.recommend_procedure("boundary condition test failure")

    assert fact_result["ema_success_score"] == 1.0
    assert assumption_result["ema_success_score"] == 0.8  # 0.5 + 0.6 * (1.0 - 0.5)
    assert assumption_result["ema_success_score"] < fact_result["ema_success_score"]


def test_unrecognized_evidence_tag_is_normalized_to_unknown_confidence():
    tenant = "test_tenant_evidence_unrecognized"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, evidence_tag="made_up_tag", critique="looked fine to me",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["ema_success_score"] == 0.85  # 0.5 + 0.7 * (1.0 - 0.5), UNKNOWN confidence
    assert result["critiques"] == ["[UNKNOWN] looked fine to me"]


def test_critique_is_prefixed_with_recognized_evidence_tag():
    tenant = "test_tenant_evidence_critique_prefix"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=False, evidence_tag="INFERENCE", critique="the retry backoff never fired",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["critiques"] == ["[INFERENCE] the retry backoff never fired"]


def test_critique_without_evidence_tag_is_stored_unprefixed():
    tenant = "test_tenant_evidence_critique_no_tag"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure", steps=["a"],
        passed=True, critique="worked as expected",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["critiques"] == ["worked as expected"]


# --- Step-level credit attribution (ShapleyFlow-inspired, simplified single-
# attribution tally; July 2026 topic-page batch, papers-of-papers research) ---

def test_blamed_step_credit_accumulates_success_and_failure_counts():
    tenant = "test_tenant_step_credit"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure",
        steps=["lead with QA", "reuse failing test"], passed=True,
        blamed_step="reuse failing test",
    )
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure",
        steps=["lead with QA", "reuse failing test"], passed=False,
        blamed_step="lead with QA",
    )
    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure",
        steps=["lead with QA", "reuse failing test"], passed=True,
        blamed_step="reuse failing test",
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["step_credit"] == {
        "reuse failing test": {"success": 2, "failure": 0},
        "lead with QA": {"success": 0, "failure": 1},
    }


def test_step_credit_defaults_to_empty_when_never_blamed():
    tenant = "test_tenant_step_credit_empty"
    engine = GraphMemoryEngine(tenant_id=tenant)

    engine.record_procedure_outcome(
        name="strategy", trigger_pattern="boundary condition test failure",
        steps=["a"], passed=True,
    )

    result = engine.recommend_procedure("boundary condition test failure")

    assert result["step_credit"] == {}
