import pytest
from unittest.mock import MagicMock
from typing import Optional, Any

from self_governance.gemini_adapter import call_safely, GeminiExecutionAdapter
from self_governance.consensus import ConsensusResult, run_consensus


# ==============================================================================
# 1. call_safely signature inspection and mismatch tests
# ==============================================================================

def test_call_safely_signatures():
    # A standard function with all expected arguments
    def f_standard(prompt: str, api_key: Optional[str], **kwargs: Any) -> tuple:
        return (prompt, api_key, kwargs)

    res = call_safely(f_standard, "hello", "secret_key", model="gemini-2.5", temp=1.0)
    assert res == ("hello", "secret_key", {"model": "gemini-2.5", "temp": 1.0})

    # Missing api_key in positional parameters, but has **kwargs
    def f_missing_key(prompt: str, **kwargs: Any) -> tuple:
        return (prompt, kwargs)

    res = call_safely(f_missing_key, "hello", "secret_key", model="gemini-2.5")
    assert res == ("hello", {"model": "gemini-2.5"})

    # Has positional arguments but no **kwargs, extra kwargs should be filtered out
    def f_no_kwargs(prompt: str, api_key: Optional[str]) -> tuple:
        return (prompt, api_key)

    res = call_safely(f_no_kwargs, "hello", "secret_key", model="gemini-2.5", temp=1.0)
    assert res == ("hello", "secret_key")

    # Positional-only parameters
    def f_positional_only(prompt: str, api_key: Optional[str], /) -> tuple:
        return (prompt, api_key)

    res = call_safely(f_positional_only, "hello", "secret_key", model="gemini-2.5")
    assert res == ("hello", "secret_key")

    # Keyword-only parameters (with default or required)
    def f_keyword_only(prompt: str, *, api_key: Optional[str] = None, model: str = "default") -> tuple:
        return (prompt, api_key, model)

    res = call_safely(f_keyword_only, "hello", "secret_key", model="gemini-2.5")
    # Vulnerability Observation: call_safely fails to pass the positional "api_key" argument
    # because it is keyword-only in the function signature, resulting in a value of None.
    assert res == ("hello", None, "gemini-2.5")

    # Zero-argument function
    def f_zero_args() -> str:
        return "zero"

    res = call_safely(f_zero_args, "hello", "secret_key", model="gemini-2.5")
    assert res == "zero"


def test_call_safely_non_callables_and_fallback():
    # If signature inspection fails (e.g. integer), calling it will also raise TypeError
    with pytest.raises(TypeError):
        call_safely(42, "hello", "secret_key")

    # Built-in function like len (ValueError on inspect.signature)
    # len only accepts one argument (prompt).
    res = call_safely(len, "hello", "secret_key", model="gemini-2.5")
    assert res == 5


def test_call_safely_magicmock():
    # Vulnerability Observation: MagicMock has signature (*args, **kwargs),
    # meaning sig.parameters contains VAR_POSITIONAL and VAR_KEYWORD,
    # but NO explicit POSITIONAL_ONLY or POSITIONAL_OR_KEYWORD parameters.
    # Therefore, call_safely sets args to [] and drops both 'prompt' and 'api_key'.
    mock_func = MagicMock()
    call_safely(mock_func, "hello", "secret_key", model="gemini-2.5")
    mock_func.assert_called_once_with(model="gemini-2.5")


# ==============================================================================
# 2. ConsensusResult tuple compatibility and immutability tests
# ==============================================================================

def test_consensus_result_tuple_limits():
    res = ConsensusResult(
        approved_roster=["agent_A"],
        final_temperature=1.0,
        final_threshold=9.0,
        prompt_tokens=10,
        completion_tokens=5
    )

    # Convert to tuple
    res_tuple = tuple(res)
    assert res_tuple == (["agent_A"], 1.0, 9.0)

    # Indexing out of bounds
    with pytest.raises(IndexError):
        _ = res[3]
    with pytest.raises(IndexError):
        _ = res[-4]

    # Indexing within bounds
    assert res[0] == ["agent_A"]
    assert res[1] == 1.0
    assert res[2] == 9.0
    assert res[-1] == 9.0
    assert res[-2] == 1.0
    assert res[-3] == ["agent_A"]


# ==============================================================================
# 3. ConsensusEngine scaling and edge case configurations
# ==============================================================================

def test_consensus_engine_voter_limits(monkeypatch):
    # Roster of size 0
    res = run_consensus([])
    assert res.approved_roster == []
    assert res.final_temperature == 1.0
    assert res.final_threshold == 9.0

    # Roster of size 10,000 (Mock mode: adapter is None)
    large_roster = [f"agent_{i}" for i in range(10000)]
    res = run_consensus(large_roster, B=1, target_tau=8.5)
    assert len(res.approved_roster) > 0

    # Roster > 100 with LLM adapter should raise ValueError
    mock_adapter = MagicMock(spec=GeminiExecutionAdapter)
    with pytest.raises(ValueError, match="initial_roster exceeds the maximum size of 100 agents"):
        run_consensus([f"agent_{i}" for i in range(101)], adapter=mock_adapter)

    # Set GEMINI_API_KEY so the adapter path is actually executed.
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    # Roster == 100 with LLM adapter should not raise ValueError
    mock_adapter._call_gemini_and_track.return_value = '{"score": 8.0, "reason": "ok"}'
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0
    res = run_consensus([f"agent_{i}" for i in range(100)], B=1, target_tau=7.5, adapter=mock_adapter)
    assert len(res.approved_roster) == 100
    assert mock_adapter._call_gemini_and_track.call_count == 100


def test_consensus_engine_adversarial_parameters():
    # Very large B (delay temperature annealing)
    res = run_consensus(["agent_A"], B=100000, target_tau=9.0)
    assert res.final_temperature == 1.0

    # Extremely high initial temperature, gamma, delta, target_tau
    res = run_consensus(
        ["agent_A"],
        B=1,
        initial_temp=10.0,
        gamma=5.0,
        delta=10.0,
        target_tau=25.0,
        T_max=15.0
    )
    assert res.final_temperature == 15.0
    assert res.final_threshold == 7.0


def test_consensus_engine_adapter_mismatch(monkeypatch):
    # Set GEMINI_API_KEY so the adapter is used
    monkeypatch.setenv("GEMINI_API_KEY", "test_key")

    mock_adapter = MagicMock()
    mock_adapter._call_gemini_and_track.return_value = None
    mock_adapter.prompt_tokens = 0
    mock_adapter.completion_tokens = 0

    # Should fallback to score=1.0 and justification "API call failed; scored as rejection."
    res = run_consensus(["agent_A"], B=1, target_tau=8.0, adapter=mock_adapter)
    assert res.approved_roster == ["agent_A"]
    # B = 1, target_tau = 8.0.
    # Iteration 1: score = 1.0. avg_score = 1.0 < 8.0.
    # Iteration 2: iteration >= B (1). tau becomes max(7.0, 8.0 - 0.5) = 7.5. temp = min(2.0, 1.0+0.1) = 1.1.
    # Iteration 3: iteration >= B (2). tau becomes max(7.0, 7.5 - 0.5) = 7.0. temp = min(2.0, 1.1+0.1) = 1.2.
    # Iteration 4: iteration >= B (3). tau is 7.0. temp is 1.3.
    # ...
    # This continues until iteration 1001.
    # At iteration 1001 (limit), it returns the maximum scored candidate.
    # final_threshold is 7.0 because it decayed to 7.0.
    assert res.final_threshold == 7.0

    # What if adapter returns a score that is not within [1.0, 10.0]?
    # E.g. score = 15.0. It should be clamped to 1.0.
    mock_adapter._call_gemini_and_track.return_value = '{"score": 15.0, "reason": "huge"}'
    res = run_consensus(["agent_A"], B=2, target_tau=9.0, adapter=mock_adapter)
    assert res.final_threshold == 7.0
    assert res.approved_roster == ["agent_A"]
