import logging
import pytest
import yaml
from unittest.mock import patch
from self_governance.billing import calculate_cost
from self_governance.consensus import run_consensus
from self_governance.gemini_adapter import GeminiExecutionAdapter

# --- Tests for calculate_cost ---

def test_calculate_cost_zero():
    """Verify calculate_cost handles 0 tokens correctly."""
    assert calculate_cost(0, 0) == 0.0

def test_calculate_cost_negative():
    """Verify calculate_cost handles negative values correctly."""
    # Negative inputs should evaluate mathematically
    assert calculate_cost(-100, -200) == pytest.approx(-0.0000675)

def test_calculate_cost_large_numbers():
    """Verify calculate_cost handles very large numbers correctly."""
    assert calculate_cost(10**12, 10**12) == pytest.approx(10**12 * (0.000000075 + 0.00000030))

def test_calculate_cost_floats():
    """Verify calculate_cost handles float-like inputs correctly."""
    assert calculate_cost(123.45, 678.90) == pytest.approx((123.45 * 0.000000075) + (678.90 * 0.00000030))

def test_calculate_cost_invalid_types():
    """Verify calculate_cost raises TypeError for invalid types."""
    with pytest.raises(TypeError):
        calculate_cost("invalid", 100)  # type: ignore
    with pytest.raises(TypeError):
        calculate_cost(100, None)       # type: ignore
    with pytest.raises(TypeError):
        calculate_cost(None, None)      # type: ignore


# --- Tests for Consensus Logging ---

def test_consensus_logging_warning_on_invalid_config(tmp_path, caplog):
    """Verify warning log when OrchestratorConfig fails to initialize due to invalid configuration."""
    invalid_config_file = tmp_path / "invalid_config.yaml"
    # Write invalid content: a list instead of a dict mapping
    invalid_config_file.write_text("- not_a_mapping")
    
    with patch("self_governance.consensus.os.getenv", return_value=None):
        with caplog.at_level(logging.WARNING, logger="self_governance.consensus"):
            _ = run_consensus(["agent_A"], config_path=str(invalid_config_file))
            
            # Verify that a warning was logged regarding the config initialization failure
            warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
            assert len(warning_records) > 0
            assert any("Failed to initialize OrchestratorConfig in ConsensusEngine" in r.message for r in warning_records)

def test_consensus_logging_info_during_consensus(caplog):
    """Verify that INFO level logs are generated during simulated consensus cycles."""
    with patch("self_governance.consensus.os.getenv", return_value=None):
        with caplog.at_level(logging.INFO, logger="self_governance.consensus"):
            # Run a short consensus cycle with B=1 to trigger threshold decay and temperature increase
            _ = run_consensus(["agent_A", "agent_B"], B=1, target_tau=8.5, delta=0.5, gamma=0.1)
            
            info_messages = [r.message for r in caplog.records if r.levelno == logging.INFO]
            assert any("Starting consensus iteration" in msg for msg in info_messages)
            assert any("evaluated score" in msg for msg in info_messages)
            assert any("average score" in msg for msg in info_messages)
            # Verify decay message is logged since B=1 and iteration goes past B
            assert any("Decaying threshold and increasing temperature" in msg for msg in info_messages)


# --- Tests for Config Path Propagation and Custom Settings ---

def test_config_propagation_respects_custom_settings(tmp_path):
    """Verify that custom max_tokens/advisor_enabled are respected by GeminiExecutionAdapter."""
    # Case 1: advisor disabled
    custom_config_data = {
        "advisor": {
            "enabled": False,
            "max_tokens": 128
        }
    }
    config_file = tmp_path / "config_disabled.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_config_data, f)
        
    adapter = GeminiExecutionAdapter(api_key="mock-api-key", config_path=str(config_file))
    
    # Check that custom settings are loaded
    assert adapter.config is not None
    assert adapter.config.advisor_enabled is False
    assert adapter.config.advisor_max_tokens == 128
    
    # Verify that advisor is disabled and skipped
    res = adapter.consult_advisor([{"role": "user", "content": "hello"}])
    assert res["status"] == "skipped"
    assert "disabled" in res["output"]

def test_config_propagation_respects_max_tokens(tmp_path):
    """Verify that custom max_tokens is passed to Gemini API call and handles MAX_TOKENS truncation."""
    # Case 2: advisor enabled with a custom max_tokens limit
    custom_config_data = {
        "advisor": {
            "enabled": True,
            "max_tokens": 512
        }
    }
    config_file = tmp_path / "config_max_tokens.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_config_data, f)
        
    adapter = GeminiExecutionAdapter(api_key="mock-api-key", config_path=str(config_file))
    
    assert adapter.config is not None
    assert adapter.config.advisor_enabled is True
    assert adapter.config.advisor_max_tokens == 512
    
    # Mock _call_gemini_and_track to check arguments and simulate a response
    mock_response = {
        "text": "This is strategically sound advice.",
        "finish_reason": "STOP"
    }
    
    with patch.object(adapter, "_call_gemini_and_track", return_value=mock_response) as mock_call:
        res = adapter.consult_advisor([{"role": "user", "content": "strategize"}])
        
        # Verify mock call arguments
        mock_call.assert_called_once()
        called_kwargs = mock_call.call_args[1]
        assert called_kwargs.get("max_output_tokens") == 512
        
        # Verify output
        assert res["status"] == "completed"
        assert res["output"] == "This is strategically sound advice."
        assert res["stop_reason"] == "end_turn"

def test_config_propagation_handles_max_tokens_truncation(tmp_path):
    """Verify that advisor handles MAX_TOKENS finish reason and appends warning message."""
    custom_config_data = {
        "advisor": {
            "enabled": True,
            "max_tokens": 256
        }
    }
    config_file = tmp_path / "config_truncation.yaml"
    with open(config_file, "w", encoding="utf-8") as f:
        yaml.safe_dump(custom_config_data, f)
        
    adapter = GeminiExecutionAdapter(api_key="mock-api-key", config_path=str(config_file))
    
    # Mock truncated response from Gemini
    mock_truncated_response = {
        "text": "Partial strategic advice",
        "finish_reason": "MAX_TOKENS"
    }
    
    with patch.object(adapter, "_call_gemini_and_track", return_value=mock_truncated_response) as mock_call:
        res = adapter.consult_advisor([{"role": "user", "content": "strategize"}])
        
        mock_call.assert_called_once()
        assert mock_call.call_args[1].get("max_output_tokens") == 256
        
        # Verify the truncation suffix and stop reason
        assert res["status"] == "completed"
        assert "truncated at max_tokens=256" in res["output"]
        assert res["stop_reason"] == "max_tokens"
