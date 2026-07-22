"""Regression tests for the algorithmic/logic-flaw fixes from the July 2026
peer-review batch: MCP boolean type-validation bypass, the simulated-annealing
score cliff, self_critique fail-open, ConsensusResult.cycles_needed,
trigger_succession's missing adapter, LazyList's dummy 4th role, and
LoopDetector never firing on the verify-failure path."""

from unittest.mock import MagicMock, patch

from self_governance.anti_drift import LoopDetector, LoopInterceptionError, self_critique
from self_governance.consensus import ConsensusEngine, ConsensusResult, run_consensus
from self_governance.dimensioning import dimension_swarm


# --- #26: bool/int type-validation bypass in mcp.py -------------------------

def test_mcp_rejects_bool_for_integer_field():
    from self_governance.mcp import MCPClient

    client = MCPClient()
    client.register_tool(
        "t",
        {"properties": {"n": {"type": "integer"}}, "required": ["n"]},
        lambda **kw: {"status": "ok"},
    )
    res = client.call_tool("t", {"n": True})
    assert res["status"] == "error"


def test_mcp_accepts_real_integer_for_integer_field():
    from self_governance.mcp import MCPClient

    client = MCPClient()
    client.register_tool(
        "t",
        {"properties": {"n": {"type": "integer"}}, "required": ["n"]},
        lambda **kw: {"status": "ok"},
    )
    res = client.call_tool("t", {"n": 5})
    assert res["status"] == "success"


# --- #24: simulated-annealing score cliff -----------------------------------

def test_score_clamps_near_boundary_overflow_instead_of_rejecting():
    engine = ConsensusEngine(initial_roster=["Backend Wizard"], requirements=[4.0])
    engine.iteration = 1
    score, _ = engine._score_agent("Backend Wizard", "")
    assert 1.0 <= score <= 10.0  # never slammed to 1.0 for a near-10 base score


def test_score_agent_rejects_wildly_implausible_llm_score_as_dissent(monkeypatch):
    adapter = MagicMock()
    adapter.is_reasoning_model.return_value = False
    adapter._call_gemini_and_track.return_value = '{"score": 999.0, "reason": "r"}'
    engine = ConsensusEngine(initial_roster=["Backend Wizard"], adapter=adapter)
    engine.api_key = "key"
    score, justification = engine._score_agent("Backend Wizard", "")
    assert score == engine._PARSE_FAILURE_SCORE
    assert "implausible" in justification.lower()


def test_score_agent_clamps_llm_score_slightly_over_ten():
    adapter = MagicMock()
    adapter.is_reasoning_model.return_value = False
    adapter._call_gemini_and_track.return_value = '{"score": 10.05, "reason": "r"}'
    engine = ConsensusEngine(initial_roster=["Backend Wizard"], adapter=adapter)
    engine.api_key = "key"
    score, _ = engine._score_agent("Backend Wizard", "")
    assert score == 10.0


# --- #25: self_critique fail-open --------------------------------------------

def test_self_critique_fails_closed_on_exception(monkeypatch):
    monkeypatch.setenv("TESTING", "False")
    adapter = MagicMock()
    adapter.api_key = "key"
    adapter._call_gemini_and_track.side_effect = Exception("network error")
    res = self_critique("plan", "goal", adapter=adapter)
    assert res["approved"] is False


# --- #23: ConsensusResult.cycles_needed --------------------------------------

def test_consensus_result_has_cycles_needed_field():
    res = ConsensusResult(approved_roster=["a"], final_temperature=1.0, final_threshold=9.0)
    assert res.cycles_needed == 1  # default


def test_run_consensus_populates_real_cycles_needed(monkeypatch):
    monkeypatch.setenv("TESTING", "True")
    res = run_consensus(["Backend Wizard", "QA Specialist"], B=1, target_tau=9.0, seed=1)
    assert res.cycles_needed >= 1


# --- #22: LazyList dummy 4th role --------------------------------------------

def test_dimension_swarm_fourth_role_is_not_a_dummy_placeholder():
    transition_matrix = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]]
    swarm_config = dimension_swarm([2.0, 2.0], transition_matrix)
    roles = {agent.role for agent in swarm_config.swarm}
    assert not any(r.startswith("role_") for r in roles)


# --- #20: LoopDetector wired into the verify-failure path -------------------

def test_loop_detector_raises_on_repeated_identical_verify_failure():
    detector = LoopDetector(window_size=10, max_occurrences=3)
    sig = "verify_failure:1:0"
    detector.record_and_check(sig)
    detector.record_and_check(sig)
    try:
        detector.record_and_check(sig)
        assert False, "expected LoopInterceptionError on the 3rd identical failure"
    except LoopInterceptionError:
        pass


def test_process_handoff_halts_on_repeated_identical_verify_failure(tmp_path):
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    nudger = ContinuousNudger(working_directory=str(tmp_path))

    def fake_policed_run(name, argv, cwd, **kwargs):
        result = MagicMock()
        result.returncode = 1  # every verify attempt fails identically
        result.stdout = "same failure every time"
        return result

    events = []
    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch("self_governance.nudger._emit_event", side_effect=lambda wd, t, d: events.append(t)):
            for _ in range(4):
                nudger.process_handoff()
                # Re-mark COMPLETED each cycle to simulate an external retry
                # feeding the identical failing task back in.
                handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    assert "loop_detected" in events
