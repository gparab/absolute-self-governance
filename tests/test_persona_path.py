import tempfile
import json
from self_governance.consensus import ConsensusEngine
from unittest.mock import patch
import os

def test_consensus_engine_custom_persona_registry():
    """Verify that ConsensusEngine reads and uses project.persona_registry."""
    custom_registry = {
        "Custom Agent": {
            "role": "Custom Agent",
            "division": "Engineering",
            "emoji": "🎭",
            "vibe": "Highly specialized custom agent.",
            "description": "Does custom things.",
            "prompt": "You are a Custom Agent."
        }
    }
    
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as registry_file:
        json.dump(custom_registry, registry_file)
        registry_file.flush()
        
        config_data = {
            "project": {
                "persona_registry": registry_file.name
            }
        }
        
        with tempfile.NamedTemporaryFile(suffix=".yaml", mode="w", delete=False) as config_file:
            import yaml
            yaml.dump(config_data, config_file)
            config_file.flush()
            
            with patch.dict(os.environ, {"GEMINI_API_KEY": "test"}):
                engine = ConsensusEngine(
                    initial_roster=["Custom Agent"],
                    config_path=config_file.name
                )
                
                # Mock _call_gemini to just return a dummy response
                with patch("self_governance.gemini_adapter.GeminiExecutionAdapter._call_gemini_and_track") as mock_call:
                    mock_call.return_value = {"text": "9.5\nJustification: Perfect score for custom agent", "finish_reason": "STOP"}
                    # Provide an empty string as peer_feedback to avoid type error
                    score, just = engine._score_agent("Custom Agent", "")
                    
                    # Verify that the mocked call received the custom prompt
                    called_prompt = mock_call.call_args[0][0]
                    assert "You are a Custom Agent." in called_prompt
            
            os.remove(config_file.name)
        os.remove(registry_file.name)
