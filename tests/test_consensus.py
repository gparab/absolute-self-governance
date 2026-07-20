import pytest
from self_governance.consensus import run_consensus


def test_consensus_deduplication():
    # Roster with duplicate elements preserving order
    initial_roster = ["agent_A", "agent_B", "agent_A", "agent_C", "agent_B"]
    res = run_consensus(initial_roster, B=3, target_tau=7.5)
    # Check that returned approved roster only contains candidates from the deduplicated set
    for agent in res.approved_roster:
        assert agent in ["agent_A", "agent_B", "agent_C"]


def test_consensus_delta_validation():
    # delta must be > 0.0
    with pytest.raises(ValueError, match="delta must be greater than 0.0"):
        run_consensus(["agent_A"], delta=0.0)
    with pytest.raises(ValueError, match="delta must be greater than 0.0"):
        run_consensus(["agent_A"], delta=-0.5)


def test_consensus_target_tau_validation():
    # target_tau must be finite
    with pytest.raises(ValueError, match="target_tau must be a finite number"):
        run_consensus(["agent_A"], target_tau=float("nan"))
    with pytest.raises(ValueError, match="target_tau must be a finite number"):
        run_consensus(["agent_A"], target_tau=float("inf"))


def test_consensus_iteration_limit():
    # Setting B = 2000 and target_tau = 9.0 forces it to not reach consensus in the first 1000 iterations
    res = run_consensus(["agent_A", "agent_B"], B=2000, target_tau=9.0)
    # It should exit after 1000 iterations (at iteration 1001)
    # The roster is returned, temp and tau shouldn't have changed since iteration < B (2000)
    assert res.final_temperature == 1.0
    assert res.final_threshold == 9.0
    assert len(res.approved_roster) > 0


def test_consensus_string_candidate_vulnerability():
    """Verify that passing a string instead of a list raises TypeError."""
    with pytest.raises(TypeError, match="initial_roster must be a list"):
        run_consensus("agent_A")


def test_consensus_non_string_elements():
    """Verify that list elements must be strings."""
    with pytest.raises(
        TypeError, match="all elements in initial_roster must be strings"
    ):
        run_consensus([123])


def test_consensus_nan_parameter_propagation():
    """Verify that passing NaN to initial_temp, gamma, or delta raises ValueError."""
    with pytest.raises(ValueError, match="initial_temp must be non-negative"):
        run_consensus(["agent_A"], initial_temp=float("nan"))

    with pytest.raises(ValueError, match="gamma must be non-negative"):
        run_consensus(["agent_A"], target_tau=9.5, gamma=float("nan"))

    with pytest.raises(ValueError, match="delta must be greater than 0.0"):
        run_consensus(["agent_A"], target_tau=9.5, delta=float("nan"))


def test_consensus_inf_parameter_propagation():
    """Verify that passing Inf to initial_temp, gamma, or delta raises ValueError."""
    with pytest.raises(ValueError, match="initial_temp must be non-negative"):
        run_consensus(["agent_A"], initial_temp=float("inf"))

    with pytest.raises(ValueError, match="gamma must be non-negative"):
        run_consensus(["agent_A"], target_tau=9.5, gamma=float("inf"))

    with pytest.raises(ValueError, match="delta must be greater than 0.0"):
        run_consensus(["agent_A"], target_tau=9.5, delta=float("inf"))


def test_consensus_iteration_limit_with_approved():
    from unittest.mock import patch

    call_count = 0

    def mock_uniform(a, b):
        nonlocal call_count
        call_count += 1
        if call_count == 2001:
            return 0.05
        elif call_count == 2002:
            return -0.06
        return -0.05

    with patch("random.Random.uniform", side_effect=mock_uniform):
        # Set B = 1005, target_tau = 8.0, and multiple agents
        res = run_consensus(["agent_A", "agent_B"], B=1005, target_tau=8.0)
        # The iteration limit > 1000 is reached at iteration 1001.
        # At iteration 1001, agent_A has score 8.05 (approved) and agent_B has score 7.94 (not approved).
        # This ensures that approved is non-empty, but avg_score is 7.995 < 8.0.
        assert res.final_temperature == 1.0
        assert res.final_threshold == 8.0
        assert res.approved_roster == ["agent_A"]


def test_consensus_convergence_rate():
    # Convergence rate test: Run 100 trials of consensus with random seeds (unseeded) and assert 100% convergence.
    for _ in range(100):
        res = run_consensus(["agent_A", "agent_B"], B=3, target_tau=8.0, seed=None)
        assert len(res.approved_roster) > 0


def test_consensus_clamping():
    # Clamping test: Run delayed consensus trials and assert the final threshold is clamped at exactly 7.0.
    # Set target_tau = 12.0, delta = 0.5, B = 3 so that decay eventually clamps at 7.0.
    res = run_consensus(["agent_A"], B=3, target_tau=12.0, delta=0.5)
    assert res.final_threshold == 7.0


def test_consensus_statistical_distribution():
    # Statistical distribution test: Run 1000 trials with 1 candidate, B=3, and target_tau=8.0.
    # Verify that the count of immediate agreements (at iteration 1) is stochastic and falls within [420, 580].
    from unittest.mock import patch
    import random

    uniform_calls = 0
    original_uniform = random.Random.uniform

    def mock_uniform(self, a, b):
        nonlocal uniform_calls
        uniform_calls += 1
        return original_uniform(self, a, b)

    immediate_agreements = 0
    with patch("random.Random.uniform", mock_uniform):
        for _ in range(1000):
            uniform_calls = 0
            # Run without a seed (seed=None) to allow stochastic behavior
            run_consensus(["agent_A"], B=3, target_tau=8.0, seed=None)
            if uniform_calls == 1:
                immediate_agreements += 1

    assert 420 <= immediate_agreements <= 580, (
        f"Immediate agreements count {immediate_agreements} not in [420, 580]"
    )


def test_consensus_temperature_clamped_by_t_max():
    # Verify that simulation temperature does not exceed T_max
    res = run_consensus(
        ["agent_A"], B=1, target_tau=20.0, initial_temp=1.0, gamma=2.0, T_max=1.5
    )
    assert res.final_temperature == 1.5


def test_advisor_nudge_and_capping():
    from unittest.mock import MagicMock
    from self_governance.consensus import run_consensus
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Mock the adapter to simulate the advisor tool and output capping
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    mock_adapter.prompt_tokens = 10
    mock_adapter.completion_tokens = 20
    mock_adapter.consult_advisor.return_value = {
        "status": "completed",
        "output": "Strategic Advisor Guidance: Keep architecture clean.",
        "stop_reason": "end_turn"
    }

    # Run consensus with the mock adapter
    # Set B=3, target_tau=8.5 so it takes multiple iterations.
    # Nudge turn defaults to 2.
    run_consensus(
        ["agent_A", "agent_B"],
        B=3,
        target_tau=8.5,
        adapter=mock_adapter
    )
    
    # Verify that the advisor was called at least once
    assert mock_adapter.consult_advisor.called

    # Test consult_advisor with truncation (finishReason = MAX_TOKENS)
    mock_adapter_trunc = GeminiExecutionAdapter(api_key="MOCK_KEY")
    mock_adapter_trunc._call_gemini_and_track = MagicMock(return_value={
        "text": "Partial advice...",
        "finish_reason": "MAX_TOKENS"
    })
    
    advisor_res = mock_adapter_trunc.consult_advisor([{"role": "user", "content": "hello"}])
    assert advisor_res["stop_reason"] == "max_tokens"
    assert "truncated" in advisor_res["output"]


def test_consensus_result_dataclass_compatibility():
    from self_governance.consensus import ConsensusResult
    from dataclasses import FrozenInstanceError

    res = ConsensusResult(
        approved_roster=["agent_A", "agent_B"],
        final_temperature=1.2,
        final_threshold=8.5,
        prompt_tokens=100,
        completion_tokens=50
    )

    # 1. Attribute access
    assert res.approved_roster == ["agent_A", "agent_B"]
    assert res.final_temperature == 1.2
    assert res.final_threshold == 8.5
    assert res.prompt_tokens == 100
    assert res.completion_tokens == 50

    # 2. Tuple unpacking
    approved, temp, threshold = res
    assert approved == ["agent_A", "agent_B"]
    assert temp == 1.2
    assert threshold == 8.5

    # 3. Indexing
    assert res[0] == ["agent_A", "agent_B"]
    assert res[1] == 1.2
    assert res[2] == 8.5
    assert len(res) == 3

    # 4. Immutability / Frozen check
    with pytest.raises(FrozenInstanceError):
        res.prompt_tokens = 200  # type: ignore


def test_consensus_engine_llm_score_parsing():
    from self_governance.consensus import ConsensusEngine

    engine = ConsensusEngine(initial_roster=["agent_A"])

    # 1. Valid JSON format
    score, reason = engine._parse_llm_score('{"score": 8.5, "reason": "Excellent match"}')
    assert score == 8.5
    assert reason == "Excellent match"

    # 2. Missing reason in JSON
    score, reason = engine._parse_llm_score('{"score": 9.2}')
    assert score == 9.2
    assert reason == "No justification provided."

    # 3. Legacy "Score: X Reason: Y" format
    score, reason = engine._parse_llm_score("Score: 6.8 Reason: Lacks sqlite concurrency skills")
    assert score == 6.8
    assert reason == "Lacks sqlite concurrency skills"

    # 4. Pure float score format
    score, reason = engine._parse_llm_score("7.2")
    assert score == 7.2
    assert reason == "No justification provided."

    # 5. Invalid format / exception fallback -- fail-closed (looper's judge-
    # verdict parsing rule): an unparseable vote must count as a dissent,
    # never silently default to a moderate-to-good score.
    score, reason = engine._parse_llm_score("Invalid response text")
    assert score == 0.0
    assert "PARSE_FAILURE" in reason

    # 6. Valid JSON but missing the score field entirely is also treated as
    # an incomplete/malformed vote, not a silent pass.
    score, reason = engine._parse_llm_score('{"reason": "looks fine"}')
    assert score == 0.0


# --- July 2026 topic-page batch, papers-of-papers research: items 1-4 ---
# --- research.google survey: task-decomposability heuristic ---

def test_estimate_task_decomposability_high_for_conjunctive_bullet_list():
    from self_governance.consensus import estimate_task_decomposability

    description = (
        "Add multi-tenant billing and per-tenant rate limiting and audit logging.\n"
        "- Track usage per tenant\n"
        "- Enforce a configurable rate limit\n"
        "- Log every billing event"
    )
    score = estimate_task_decomposability(description)

    assert score > 0.5


def test_estimate_task_decomposability_low_for_sequential_chain():
    from self_governance.consensus import estimate_task_decomposability

    description = (
        "First, parse the config file. Then validate the schema. "
        "Once validated, apply the migration, and finally verify the result "
        "depends on the migration having completed."
    )
    score = estimate_task_decomposability(description)

    assert score < 0.5


def test_estimate_task_decomposability_defaults_to_midpoint_with_no_signal():
    from self_governance.consensus import estimate_task_decomposability

    assert estimate_task_decomposability("Fix a typo in the README.") == 0.5
    assert estimate_task_decomposability("") == 0.5
    assert estimate_task_decomposability("   ") == 0.5


def test_weighted_average_defaults_to_flat_mean():
    from self_governance.consensus import _weighted_average

    assert _weighted_average({"a": 6.0, "b": 8.0}) == 7.0
    assert _weighted_average({"a": 6.0, "b": 8.0}, weights={}) == 7.0


def test_weighted_average_applies_calibration_weights():
    from self_governance.consensus import _weighted_average

    # 'b' weighted 3x -- pulls the average toward its score.
    result = _weighted_average({"a": 6.0, "b": 8.0}, weights={"b": 3.0})
    assert result == pytest.approx((6.0 * 1.0 + 8.0 * 3.0) / 4.0)


def test_weighted_average_missing_agent_defaults_to_weight_one():
    from self_governance.consensus import _weighted_average

    result = _weighted_average({"a": 6.0, "b": 8.0}, weights={"a": 2.0})
    assert result == pytest.approx((6.0 * 2.0 + 8.0 * 1.0) / 3.0)


def test_detect_groupthink_flags_unanimous_near_identical_justifications():
    from self_governance.consensus import _detect_groupthink

    justifications = {
        "agent_A": {"justification": "strong fit for backend concurrency and retries"},
        "agent_B": {"justification": "strong fit for backend concurrency retries"},
    }
    assert _detect_groupthink(justifications, ["agent_A", "agent_B"]) is True


def test_detect_groupthink_not_flagged_for_distinct_reasoning():
    from self_governance.consensus import _detect_groupthink

    justifications = {
        "agent_A": {"justification": "strong fit for backend concurrency"},
        "agent_B": {"justification": "handles security review well under pressure"},
    }
    assert _detect_groupthink(justifications, ["agent_A", "agent_B"]) is False


def test_detect_groupthink_not_flagged_for_single_approved_agent():
    from self_governance.consensus import _detect_groupthink

    justifications = {"agent_A": {"justification": "strong fit"}}
    assert _detect_groupthink(justifications, ["agent_A"]) is False


def test_consensus_result_flags_groupthink_on_unanimous_near_identical_round(monkeypatch):
    """End-to-end: a round where every approved agent's justification is
    near-identical sets ConsensusResult.groupthink_suspected."""
    from unittest.mock import MagicMock
    from self_governance.consensus import ConsensusEngine
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0
    mock_adapter.is_reasoning_model.return_value = False
    mock_adapter._call_gemini_and_track.side_effect = [
        '{"score": 9.0, "reason": "strong fit for backend concurrency and retries"}',
        '{"score": 9.0, "reason": "strong fit for backend concurrency retries"}',
    ]

    engine = ConsensusEngine(
        initial_roster=["agent_A", "agent_B"], B=1, target_tau=8.0, adapter=mock_adapter,
    )
    result = engine.run()

    assert result.approved_roster == ["agent_A", "agent_B"]
    assert result.groupthink_suspected is True


def test_anti_sycophancy_omits_numeric_score_from_peer_feedback(monkeypatch):
    """Anti-sycophancy (Sharma et al. 2023): the peer-feedback shown before
    a later round must not display the earlier round's numeric score when
    anti_sycophancy=True, to avoid anchoring later votes on the visible
    prior consensus number."""
    from unittest.mock import MagicMock
    from self_governance.consensus import ConsensusEngine
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0
    mock_adapter.is_reasoning_model.return_value = False
    mock_adapter._call_gemini_and_track.side_effect = [
        '{"score": 5.0, "reason": "round1 reasoning"}',
        '{"score": 9.0, "reason": "round2 reasoning"}',
    ]

    engine = ConsensusEngine(
        initial_roster=["agent_A"], B=1, target_tau=9.0, adapter=mock_adapter,
        anti_sycophancy=True,
    )
    engine.run()

    assert mock_adapter._call_gemini_and_track.call_count == 2
    round2_prompt = mock_adapter._call_gemini_and_track.call_args_list[1][0][0]
    assert "was rated" not in round2_prompt
    assert "round1 reasoning" in round2_prompt


def test_debate_phase_triggers_on_contested_vote_and_targets_dissenter(monkeypatch):
    """Multiagent debate (Du et al. 2023): a contested round (avg score
    within debate_margin of tau) triggers one extra round that puts the
    strongest dissenting justification directly to every agent, and that
    round's scores decide the outcome."""
    from unittest.mock import MagicMock
    from self_governance.consensus import ConsensusEngine
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0
    mock_adapter.is_reasoning_model.return_value = False
    mock_adapter._call_gemini_and_track.side_effect = [
        '{"score": 7.0, "reason": "agent_A round1 objection"}',
        '{"score": 8.0, "reason": "agent_bad round1"}',
        '{"score": 8.5, "reason": "agent_A debate"}',
        '{"score": 8.5, "reason": "agent_bad debate"}',
    ]

    engine = ConsensusEngine(
        initial_roster=["agent_A", "agent_bad"], B=5, target_tau=8.0, adapter=mock_adapter,
        enable_debate=True, debate_margin=1.0,
    )
    result = engine.run()

    assert mock_adapter._call_gemini_and_track.call_count == 4
    debate_prompt = mock_adapter._call_gemini_and_track.call_args_list[2][0][0]
    assert "Debate round" in debate_prompt
    assert "agent_A round1 objection" in debate_prompt
    assert result.approved_roster == ["agent_A", "agent_bad"]


def test_debate_phase_off_by_default_does_not_trigger(monkeypatch):
    from unittest.mock import MagicMock
    from self_governance.consensus import ConsensusEngine
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0
    mock_adapter.is_reasoning_model.return_value = False
    mock_adapter._call_gemini_and_track.side_effect = [
        '{"score": 7.0, "reason": "agent_A round1"}',
        '{"score": 8.0, "reason": "agent_bad round1"}',
    ]

    engine = ConsensusEngine(
        # avg (7.0+8.0)/2 == 7.5 == target_tau: passes on round 1, so a 3rd
        # call would only happen if a debate phase fired despite
        # enable_debate defaulting to False.
        initial_roster=["agent_A", "agent_bad"], B=1, target_tau=7.5, adapter=mock_adapter,
    )
    result = engine.run()

    # Only agent_bad clears tau individually (7.0 < 7.5 < 8.0); the point of
    # this test is the call_count, confirming no debate round fired.
    assert result.approved_roster == ["agent_bad"]
    assert mock_adapter._call_gemini_and_track.call_count == 2

