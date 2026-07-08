from unittest.mock import MagicMock
from self_governance.agency_agents_adapter import get_persona
from self_governance.dimensioning import dimension_swarm
from self_governance.consensus import run_consensus


def test_get_persona():
    # Test valid persona lookup
    p_dev = get_persona("Backend Wizard")
    assert p_dev["role"] == "Backend Wizard"
    assert p_dev["division"] == "Engineering"
    assert "Wizard" in p_dev["prompt"]

    # Test fallback
    p_fake = get_persona("Unknown Spec")
    assert p_fake["role"] == "Unknown Spec"
    assert p_fake["division"] == "General"


def test_dimensioning_with_personas():
    # 1. Dimension a swarm
    config = dimension_swarm(
        [1.0, 1.0, 1.0], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )
    assert len(config.swarm) == 9

    # Verify LazyList resolved to actual agency agent roles
    roles = [agent.role for agent in config.swarm]
    assert "Backend Wizard" in roles
    assert "QA Specialist" in roles
    assert "Security Auditor" in roles

    prompts = [agent.prompt for agent in config.swarm]
    assert any("Wizard" in p for p in prompts)
    assert any("QA" in p for p in prompts)
    assert any("Security" in p for p in prompts)


def test_consensus_with_personas(monkeypatch):
    # Mock gemini adapter to see if prompt receives persona info
    mock_adapter = MagicMock()
    mock_adapter._call_gemini_and_track.return_value = (
        "Score: 9.0\nReason: Looks perfect"
    )

    monkeypatch.setenv("GEMINI_API_KEY", "mock_key")

    roster = ["Backend Wizard", "Security Auditor"]
    result = run_consensus(roster, adapter=mock_adapter)

    assert len(result.approved_roster) > 0
    # Ensure calls were made and prompt included the persona guidelines
    calls = mock_adapter._call_gemini_and_track.call_args_list
    assert len(calls) > 0
    first_call_prompt = calls[0][0][0]
    assert "Guidelines:" in first_call_prompt


def test_dynamic_capability_injection(monkeypatch):
    # 1. Dimension a swarm with requirements that trigger specific capabilities
    # requirements[0] > 0.0 -> sqlite_concurrency
    # requirements[1] > 0.0 -> hmac_verification, path_traversal_hardening
    # requirements[2] > 0.0 -> pytest_coverage
    config = dimension_swarm(
        [1.0, 1.0, 1.0], [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    )

    # Get first agent from the swarm
    agent = config.swarm[0]

    # Assert capabilities list is resolved and stored
    assert "sqlite_concurrency" in agent.capabilities
    assert "hmac_verification" in agent.capabilities
    assert "path_traversal_hardening" in agent.capabilities
    assert "pytest_coverage" in agent.capabilities

    # Verify the guidelines prompt block is appended to the system prompt
    assert "Injected Capabilities / Skills Guidelines" in agent.prompt
    assert "SQLite" in agent.prompt
    assert "timing-attack" in agent.prompt
    assert "path traversal" in agent.prompt

    # 2. Verify capability injection in consensus loop
    mock_adapter = MagicMock()
    mock_adapter._call_gemini_and_track.return_value = (
        "Score: 8.5\nReason: Strong capabilities match."
    )
    monkeypatch.setenv("GEMINI_API_KEY", "mock_key")

    roster = ["Backend Wizard"]
    # Pass requirements to run_consensus to trigger capability injection
    _ = run_consensus(roster, adapter=mock_adapter, requirements=[1.0, 1.0, 1.0])

    calls = mock_adapter._call_gemini_and_track.call_args_list
    assert len(calls) > 0
    deliberation_prompt = calls[0][0][0]

    # Verify capability guidelines are injected into deliberation context
    assert "Capabilities/Skills Guidelines" in deliberation_prompt
    assert "SQLite" in deliberation_prompt
