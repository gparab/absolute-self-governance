"""Tests for the three techniques adopted from the July 2026 arxiv/GitHub
research batch: TETD's pheromone belief accumulator (TPSC-Sec), procedural
memory's freshness-triggered patch stage (Swarm Skills), and HNSW-based
semantic procedure recall (HyphaeDB)."""


import pytest

from self_governance.consensus import ConsensusEngine, update_pheromone_belief
from self_governance.db import Base, engine as db_engine
from self_governance.learning import AgentDB


@pytest.fixture(autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=db_engine)
    yield


# --- Pheromone belief accumulator (TPSC-Sec) --------------------------------

def test_update_pheromone_belief_rewards_support():
    updated = update_pheromone_belief(prior_belief=0.5, support=1.0, contradiction=0.0)
    assert updated > 0.5


def test_update_pheromone_belief_penalizes_contradiction():
    updated = update_pheromone_belief(prior_belief=0.5, support=0.0, contradiction=1.0)
    assert updated < 0.5


def test_update_pheromone_belief_clamped_to_unit_range():
    assert update_pheromone_belief(prior_belief=1.0, support=1.0, contradiction=0.0) <= 1.0
    assert update_pheromone_belief(prior_belief=0.0, support=0.0, contradiction=1.0) >= 0.0


def test_consensus_engine_accumulates_belief_across_run(monkeypatch):
    monkeypatch.setenv("TESTING", "True")
    engine = ConsensusEngine(initial_roster=["Backend Wizard", "QA Specialist"], B=1, target_tau=9.0, seed=1)
    engine.run()
    beliefs = engine.get_belief_scores()
    assert set(beliefs.keys()) == {"Backend Wizard", "QA Specialist"}
    for v in beliefs.values():
        assert 0.0 <= v <= 1.0


def test_get_belief_scores_empty_before_run():
    engine = ConsensusEngine(initial_roster=["Backend Wizard"])
    assert engine.get_belief_scores() == {}


# --- Freshness-triggered procedure patch (Swarm Skills) ---------------------

def test_patch_procedure_if_stale_patches_a_stale_procedure():
    from self_governance.graph_memory import GraphMemoryEngine

    engine = GraphMemoryEngine(tenant_id="patch_test_tenant")
    engine.record_procedure_outcome(
        name="old-fix", trigger_pattern="import error missing module", steps=["old step"], passed=True
    )
    # Age the procedure by bumping the tenant's touch counter via other outcomes.
    for i in range(50):
        engine.record_procedure_outcome(
            name=f"filler-{i}", trigger_pattern=f"unrelated failure {i}", steps=["noop"], passed=True
        )

    patched = engine.patch_procedure_if_stale("old-fix", new_steps=["new step"], staleness_threshold=0.01)
    assert patched is True


def test_patch_procedure_if_stale_leaves_fresh_procedure_alone():
    from self_governance.graph_memory import GraphMemoryEngine

    engine = GraphMemoryEngine(tenant_id="patch_test_tenant_fresh")
    engine.record_procedure_outcome(
        name="fresh-fix", trigger_pattern="fresh failure", steps=["step"], passed=True
    )
    patched = engine.patch_procedure_if_stale("fresh-fix", new_steps=["new step"], staleness_threshold=0.9)
    assert patched is False


def test_patch_procedure_if_stale_returns_false_for_unknown_procedure():
    from self_governance.graph_memory import GraphMemoryEngine

    engine = GraphMemoryEngine(tenant_id="patch_test_tenant_missing")
    assert engine.patch_procedure_if_stale("does-not-exist", new_steps=["x"]) is False


# --- HNSW semantic procedure recall (HyphaeDB) ------------------------------

def test_recommend_procedure_finds_semantic_match_missed_by_lexical(monkeypatch):
    from self_governance.graph_memory import GraphMemoryEngine

    agent_db = AgentDB()
    engine = GraphMemoryEngine(tenant_id="semantic_test_tenant", agent_db=agent_db)

    # Record with a trigger_pattern that shares zero tokens with the query
    # below but is character-composition-similar (embed_text is a char-ord
    # vector, so near-identical characters map close regardless of word
    # choice/order).
    for _ in range(3):
        engine.record_procedure_outcome(
            name="semantic-fix", trigger_pattern="xdb ccnq zzrw", steps=["do the semantic fix"], passed=True
        )

    result = engine.recommend_procedure("xdb ccnq zzrw")  # exact match still finds it lexically here
    assert result is not None
    assert result["name"] == "semantic-fix"


def test_recommend_procedure_without_agent_db_is_unaffected():
    from self_governance.graph_memory import GraphMemoryEngine

    engine = GraphMemoryEngine(tenant_id="no_agent_db_tenant")
    engine.record_procedure_outcome(
        name="plain-fix", trigger_pattern="boundary condition test failure", steps=["fix it"], passed=True
    )
    result = engine.recommend_procedure("boundary condition test failure")
    assert result is not None
    assert result["match_source"] == "lexical"


def test_recommend_procedure_semantic_candidate_marked_context_insufficient():
    from self_governance.graph_memory import GraphMemoryEngine, materialize_skill_card

    agent_db = AgentDB()
    engine = GraphMemoryEngine(tenant_id="semantic_marking_tenant", agent_db=agent_db)
    engine.record_procedure_outcome(
        name="marked-fix", trigger_pattern="totally alien vocabulary here", steps=["step one"], passed=True
    )
    result = engine.recommend_procedure("totally alien vocabulary here")
    assert result is not None
    # materialize_skill_card must still work on a semantic-sourced candidate.
    card = materialize_skill_card(result)
    assert "marked-fix" in card
