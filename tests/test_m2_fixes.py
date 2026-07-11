import os
import logging
import pytest
from unittest.mock import MagicMock, patch
from self_governance.billing import calculate_cost
from self_governance.config import OrchestratorConfig
from self_governance.consensus import ConsensusEngine, run_consensus
from self_governance.nudger import ContinuousNudger


def test_calculate_cost():
    """Verify that calculate_cost returns the correct price calculation."""
    # (1000 * 0.000000075) + (2000 * 0.00000030)
    # = 0.000075 + 0.0006 = 0.000675
    assert calculate_cost(1000, 2000) == pytest.approx(0.000675)
    assert calculate_cost(0, 0) == 0.0


def test_config_path_persisted():
    """Verify that OrchestratorConfig persists config_path."""
    config = OrchestratorConfig(config_path="/dummy/path.yaml")
    assert config.config_path == "/dummy/path.yaml"


def test_consensus_engine_config_propagation():
    """Verify config_path is propagated in ConsensusEngine and GeminiExecutionAdapter."""
    with patch("self_governance.consensus.GeminiExecutionAdapter") as mock_adapter_class, \
         patch("self_governance.consensus.OrchestratorConfig") as mock_config_class:
        
        # Set environment variable so adapter is instantiated
        with patch.dict(os.environ, {"GEMINI_API_KEY": "test-key"}):
            _ = ConsensusEngine(
                initial_roster=["agent_A"],
                config_path="/dummy/path.yaml"
            )
            
            # Verify OrchestratorConfig is instantiated with the config_path
            mock_config_class.assert_called_with("/dummy/path.yaml")
            
            # Verify GeminiExecutionAdapter is instantiated with the config_path
            mock_adapter_class.assert_called_with(api_key="test-key", config_path="/dummy/path.yaml")


def test_nudger_propagates_config_path():
    """Verify that ContinuousNudger propagates the config path to run_consensus."""
    config = OrchestratorConfig(config_path="/dummy/path.yaml")
    nudger = ContinuousNudger(working_directory=".", config=config)
    
    with patch("self_governance.nudger.run_consensus") as mock_run_consensus:
        mock_result = MagicMock()
        mock_result.approved_roster = ["agent_A"]
        mock_result.final_temperature = 1.0
        mock_result.final_threshold = 0.9
        mock_result.cycles_needed = 1
        mock_run_consensus.return_value = mock_result
        
        handoff_content = "candidates:\n  - agent_A"
        nudger.trigger_succession(handoff_content)
        
        # Assert that run_consensus was called with config_path propagated
        called_kwargs = mock_run_consensus.call_args[1]
        assert called_kwargs.get("config_path") == "/dummy/path.yaml"


def test_consensus_logging(caplog):
    """Verify that ConsensusEngine logs appropriate messages during iteration."""
    # Use mock/offline mode (no GEMINI_API_KEY) so mock calculations are used
    with caplog.at_level(logging.INFO, logger="self_governance.consensus"):
        _ = run_consensus(["agent_A", "agent_B"], B=2, target_tau=8.5)
        
        # Check iteration start and score logs exist
        assert any("Starting consensus iteration" in record.message for record in caplog.records)
        assert any("evaluated score" in record.message for record in caplog.records)
        assert any("average score" in record.message for record in caplog.records)
        assert any("Consensus successfully achieved" in record.message or "Decaying threshold" in record.message for record in caplog.records)


def test_consensus_exception_logging(caplog):
    """Verify that ConsensusEngine logs warnings when exceptions are caught."""
    engine = ConsensusEngine(["agent_A"])
    
    with caplog.at_level(logging.WARNING, logger="self_governance.consensus"):
        # Test JSON parsing exception
        score, justification = engine._parse_llm_score("invalid json string")
        assert score == 7.5
        assert justification == "No justification provided."
        assert any("Failed to parse LLM response as JSON" in record.message for record in caplog.records)
        
        caplog.clear()
        
        # Test format parsing exception (Split reason exception)
        # Score: is present but split/Reason: raises exception because it can't be converted to float
        score, justification = engine._parse_llm_score("Score: abc Reason: none")
        assert score == 7.5
        assert any("Failed to parse Score/Reason text formatting" in record.message for record in caplog.records)

        caplog.clear()
        
        # Test direct float parsing exception
        # No Score: but direct float conversion fails
        score, justification = engine._parse_llm_score("not a float")
        assert score == 7.5
        assert any("Failed to parse response as float" in record.message for record in caplog.records)
