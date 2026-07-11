import logging
import pytest
import yaml
from unittest.mock import patch
from self_governance.billing import calculate_cost
from self_governance.consensus import ConsensusEngine, run_consensus
from self_governance.gemini_adapter import GeminiExecutionAdapter


# =====================================================================
# 1. Stress Tests for calculate_cost
# =====================================================================

def test_calculate_cost_zero():
    """Verify that calculate_cost handles 0 tokens correctly."""
    assert calculate_cost(0, 0) == 0.0


def test_calculate_cost_negative():
    """Verify that calculate_cost handles negative token amounts by performing standard calculation."""
    # -100 * 0.000000075 + -200 * 0.00000030 = -0.0000075 - 0.00006 = -0.0000675
    assert calculate_cost(-100, -200) == pytest.approx(-0.0000675)


def test_calculate_cost_large():
    """Verify that calculate_cost handles large token numbers correctly."""
    # 1_000_000_000 * 0.000000075 + 2_000_000_000 * 0.00000030 = 75 + 600 = 675.0
    assert calculate_cost(10**9, 2 * 10**9) == pytest.approx(675.0)


def test_calculate_cost_floats():
    """Verify that calculate_cost handles float/float-like values correctly."""
    # 1000.5 * 0.000000075 + 2000.5 * 0.00000030 = 0.0000750375 + 0.00060015 = 0.0006751875
    assert calculate_cost(1000.5, 2000.5) == pytest.approx(0.0006751875)


def test_calculate_cost_invalid_types():
    """Verify that calculate_cost raises TypeError when passed invalid types (strings, lists)."""
    with pytest.raises(TypeError):
        calculate_cost("1000", 2000)
    with pytest.raises(TypeError):
        calculate_cost(1000, "2000")
    with pytest.raises(TypeError):
        calculate_cost([1000], 2000)


# =====================================================================
# 2. Logging Level Verification during simulated consensus cycles
# =====================================================================

def test_consensus_logging_levels(caplog):
    """Verify that standard logging correctly outputs messages on warning/info levels during simulated consensus cycles."""
    # We will run a multi-iteration consensus by setting B=1, target_tau=9.5.
    # The mock score will be ~8.0, so it will decay tau from 9.5 to 9.0 to 8.5 to 8.0, etc.
    with caplog.at_level(logging.INFO, logger="self_governance.consensus"):
        run_consensus(["agent_A", "agent_B"], B=1, target_tau=9.5, delta=0.5)
        
        # Verify info messages
        records = [r.message for r in caplog.records]
        assert any("Starting consensus iteration" in msg for msg in records)
        assert any("evaluated score" in msg for msg in records)
        assert any("average score" in msg for msg in records)
        assert any("Decaying threshold and increasing temperature" in msg for msg in records)
        assert any("Consensus successfully achieved" in msg for msg in records)


def test_consensus_warning_log_config(caplog, tmp_path):
    """Verify that ConsensusEngine logs warnings when config file cannot be loaded/initialized."""
    # Write a malformed config file (invalid YAML)
    malformed_config = tmp_path / "malformed_config.yaml"
    with open(malformed_config, "w", encoding="utf-8") as f:
        f.write("consensus:\n  buffer_limit: :invalid:")
        
    with caplog.at_level(logging.WARNING, logger="self_governance.consensus"):
        # Initialize ConsensusEngine with the path to the malformed config file
        _ = ConsensusEngine(["agent_A"], config_path=str(malformed_config))
        
        # Assert warning message about configuration loading failure is present
        assert any(
            "Failed to initialize OrchestratorConfig in ConsensusEngine; falling back to default advisor configurations."
            in r.message for r in caplog.records
        )


def test_consensus_warning_log_invalid_scores(caplog):
    """Verify that ConsensusEngine logs warnings for various LLM response formatting errors."""
    engine = ConsensusEngine(["agent_A"])
    
    with caplog.at_level(logging.WARNING, logger="self_governance.consensus"):
        # 1. Invalid JSON
        caplog.clear()
        engine._parse_llm_score("Malformed JSON Response")
        assert any("Failed to parse LLM response as JSON" in r.message for r in caplog.records)
        
        # 2. Invalid Split / Text format
        caplog.clear()
        engine._parse_llm_score("Score: non-numeric Reason: none")
        assert any("Failed to parse Score/Reason text formatting" in r.message for r in caplog.records)
        
        # 3. Direct Float Conversion Failure
        caplog.clear()
        engine._parse_llm_score("Not a float number")
        assert any("Failed to parse response as float" in r.message for r in caplog.records)


# =====================================================================
# 3. Config Path Propagation and Custom Settings Verification
# =====================================================================

def test_config_propagation_custom_settings(tmp_path):
    """Verify that custom settings in config.yaml are successfully propagated and respected by GeminiExecutionAdapter."""
    # 1. Custom settings: Advisor disabled
    config_disabled_path = tmp_path / "config_disabled.yaml"
    with open(config_disabled_path, "w", encoding="utf-8") as f:
        yaml.dump({
            "advisor": {
                "enabled": False,
                "max_tokens": 128
            }
        }, f)
        
    adapter_disabled = GeminiExecutionAdapter(api_key="mock-key", config_path=str(config_disabled_path))
    assert adapter_disabled.config is not None
    assert adapter_disabled.config.advisor_enabled is False
    assert adapter_disabled.config.advisor_max_tokens == 128
    
    # Assert that consult_advisor skips the API call when disabled
    result_disabled = adapter_disabled.consult_advisor([])
    assert result_disabled["status"] == "skipped"
    assert "disabled by configuration" in result_disabled["output"]
    
    # 2. Custom settings: Advisor enabled, max_tokens customized
    config_enabled_path = tmp_path / "config_enabled.yaml"
    with open(config_enabled_path, "w", encoding="utf-8") as f:
        yaml.dump({
            "advisor": {
                "enabled": True,
                "max_tokens": 512
            }
        }, f)
        
    adapter_enabled = GeminiExecutionAdapter(api_key="mock-key", config_path=str(config_enabled_path))
    assert adapter_enabled.config is not None
    assert adapter_enabled.config.advisor_enabled is True
    assert adapter_enabled.config.advisor_max_tokens == 512
    
    # Assert that consult_advisor calls _call_gemini_and_track with correct max_output_tokens
    with patch.object(adapter_enabled, "_call_gemini_and_track") as mock_track:
        mock_track.return_value = {
            "text": "Strategic advice",
            "finish_reason": "STOP"
        }
        result_enabled = adapter_enabled.consult_advisor([{"role": "user", "content": "Hello"}])
        
        # Verify _call_gemini_and_track was called with max_output_tokens set to the custom 512
        mock_track.assert_called_once()
        called_kwargs = mock_track.call_args[1]
        assert called_kwargs.get("max_output_tokens") == 512
        assert result_enabled["status"] == "completed"
        assert result_enabled["output"] == "Strategic advice"
