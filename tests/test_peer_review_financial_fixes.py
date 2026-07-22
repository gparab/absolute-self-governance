"""Regression tests for the financial/API-runaway-risk fixes from the July
2026 peer-review batch: GitHub webhook retry billing loop, cost-tiering
uncertainty-proxy kwargs bypass, and AgentBudget delegation conservation."""

from unittest.mock import MagicMock, patch

import pytest

from self_governance.github_app import _handle_issues_event
from self_governance.nudger import SimulationException
from self_governance.policy import AgentBudget
from self_governance.providers import tiered_call


# --- #17: webhook retry billing loop ----------------------------------------

def test_handle_issues_event_returns_rejection_not_raise_on_simulation_exception(monkeypatch):
    payload_opened = {
        "action": "opened",
        "issue": {"title": "cve security", "body": "details"},
    }
    mock_nudger = MagicMock()
    mock_nudger.config.webhook_matrix = [[1.0, 1.0]]
    mock_nudger.trigger_succession.side_effect = SimulationException("Council Sandbox rejected handoff: FAIL: too risky")
    monkeypatch.setattr("self_governance.github_app.nudger", mock_nudger)

    mock_adapter_cls = MagicMock()
    mock_adapter_inst = mock_adapter_cls.return_value
    mock_adapter_inst.prompt_tokens = 50
    mock_adapter_inst.completion_tokens = 30
    monkeypatch.setattr("self_governance.gemini_adapter.GeminiExecutionAdapter", mock_adapter_cls)

    db_mock = MagicMock()
    with patch("self_governance.github_app.SessionLocal", return_value=db_mock):
        result = _handle_issues_event(payload_opened, "t1")

    assert result is not None
    assert result["status"] == "rejected"
    db_mock.add.assert_called()  # usage was still billed


def test_handle_issues_event_still_raises_on_genuine_errors(monkeypatch):
    payload_opened = {
        "action": "opened",
        "issue": {"title": "cve security", "body": "details"},
    }
    mock_nudger = MagicMock()
    mock_nudger.config.webhook_matrix = [[1.0, 1.0]]
    mock_nudger.trigger_succession.side_effect = RuntimeError("Consensus failure")
    monkeypatch.setattr("self_governance.github_app.nudger", mock_nudger)

    mock_adapter_cls = MagicMock()
    mock_adapter_inst = mock_adapter_cls.return_value
    mock_adapter_inst.prompt_tokens = 50
    mock_adapter_inst.completion_tokens = 30
    monkeypatch.setattr("self_governance.gemini_adapter.GeminiExecutionAdapter", mock_adapter_cls)

    db_mock = MagicMock()
    with patch("self_governance.github_app.SessionLocal", return_value=db_mock):
        with pytest.raises(RuntimeError):
            _handle_issues_event(payload_opened, "t1")


# --- #18: cost-tiering uncertainty proxy kwargs bypass ----------------------

def test_tiered_call_probe_receives_structural_kwargs():
    provider = MagicMock()
    seen_kwargs = []

    def fake_generate_content(prompt, api_key, model=None, **kwargs):
        seen_kwargs.append(kwargs)
        return {"text": "same"}

    provider.generate_content.side_effect = fake_generate_content
    tiered_call(
        provider, "prompt", api_key="k",
        system_instruction="be terse", response_schema={"type": "OBJECT"},
    )

    # First two calls are the uncertainty probes -- both must have received
    # the structural kwargs, not just the final draft/escalated call.
    assert seen_kwargs[0].get("system_instruction") == "be terse"
    assert seen_kwargs[0].get("response_schema") == {"type": "OBJECT"}
    assert seen_kwargs[1].get("system_instruction") == "be terse"


def test_tiered_call_probes_use_fixed_temperature_regardless_of_caller():
    provider = MagicMock()
    seen_temps = []

    def fake_generate_content(prompt, api_key, model=None, **kwargs):
        seen_temps.append(kwargs.get("temperature"))
        return {"text": "same"}

    provider.generate_content.side_effect = fake_generate_content
    tiered_call(provider, "prompt", api_key="k", temperature=0.0)

    assert seen_temps[0] == 0.7
    assert seen_temps[1] == 0.7


# --- #19: AgentBudget delegation conservation bypass ------------------------

def test_agent_budget_child_budget_deducts_from_parent():
    parent = AgentBudget(max_actions=100)
    parent.child_budget(40)
    assert parent.remaining == 60


def test_agent_budget_cannot_spawn_unlimited_children():
    parent = AgentBudget(max_actions=100)
    parent.child_budget(100)
    with pytest.raises(ValueError):
        parent.child_budget(1)
