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
    with pytest.raises(TypeError, match="all elements in initial_roster must be strings"):
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

    with patch('random.Random.uniform', side_effect=mock_uniform):
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
    with patch('random.Random.uniform', mock_uniform):
        for _ in range(1000):
            uniform_calls = 0
            # Run without a seed (seed=None) to allow stochastic behavior
            run_consensus(["agent_A"], B=3, target_tau=8.0, seed=None)
            if uniform_calls == 1:
                immediate_agreements += 1

    assert 420 <= immediate_agreements <= 580, f"Immediate agreements count {immediate_agreements} not in [420, 580]"



