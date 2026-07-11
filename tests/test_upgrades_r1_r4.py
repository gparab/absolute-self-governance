import json
import yaml
import pytest
from unittest.mock import MagicMock, patch
from pydantic import ValidationError

from self_governance.models import Agent
from self_governance.gemini_adapter import GeminiExecutionAdapter, call_gemini_with_metadata
from self_governance.consensus import ConsensusEngine
from self_governance.ability_loader import AbilityLoader
from self_governance.config import OrchestratorConfig
from self_governance.nudger import ContinuousNudger


# ===========================================================================
# 1. Model-Aware LLM Adapters Tests
# ===========================================================================

def test_agent_model_developer_message():
    """Verify that the Agent class supports the developer_message field."""
    # Test initialization
    agent = Agent(
        role="Wizard",
        prompt="Standard Prompt",
        capabilities=["magic"],
        quality_gate=None,
        developer_message="Reasoning Prompt"
    )
    assert agent.role == "Wizard"
    assert agent.prompt == "Standard Prompt"
    assert agent.capabilities == ["magic"]
    assert agent.developer_message == "Reasoning Prompt"

    # Subscript access (__getitem__, __setitem__)
    assert agent["developer_message"] == "Reasoning Prompt"
    agent["developer_message"] = "New Reasoning Prompt"
    assert agent.developer_message == "New Reasoning Prompt"
    assert "developer_message" in agent

    # keys, values, items, len
    assert "developer_message" in agent.keys()
    assert "New Reasoning Prompt" in agent.values()
    assert any(item[0] == "developer_message" and item[1] == "New Reasoning Prompt" for item in agent.items())
    assert len(agent) == 5

    # Positional initialization
    agent_pos = Agent("Wizard", "Standard Prompt", ["magic"], None, "Reasoning Prompt")
    assert agent_pos.developer_message == "Reasoning Prompt"


def test_is_reasoning_model():
    """Verify that is_reasoning_model detects reasoning models correctly."""
    adapter = GeminiExecutionAdapter()
    
    assert adapter.is_reasoning_model("o1-mini") is True
    assert adapter.is_reasoning_model("o3-mini") is True
    assert adapter.is_reasoning_model("gemini-2.0-flash-thinking-exp") is True
    assert adapter.is_reasoning_model("reasoning-model-v1") is True
    
    assert adapter.is_reasoning_model("gemini-2.5-flash") is False
    assert adapter.is_reasoning_model("gpt-4o") is False
    assert adapter.is_reasoning_model("") is False
    assert adapter.is_reasoning_model(None) is False


@patch("urllib.request.urlopen")
def test_call_gemini_with_metadata_reasoning(mock_urlopen):
    """Verify that call_gemini_with_metadata strips temperature and maps roles for reasoning models."""
    # Mock response
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "Mocked Reasoning Response"}]}
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    # Test standard Gemini API payload structure for reasoning model
    res = call_gemini_with_metadata(
        prompt="Hello",
        api_key="mock-key",
        model="o1-mini",
        temperature=0.7,
        developer_message="Dev Message",
        is_reasoning=True
    )
    assert res["text"] == "Mocked Reasoning Response"
    
    # Verify urlopen args
    args, kwargs = mock_urlopen.call_args
    req = args[0]
    payload = json.loads(req.data.decode())
    
    # Assert temperature is stripped/omitted in generationConfig
    if "generationConfig" in payload:
        assert "temperature" not in payload["generationConfig"]
    
    # Assert developer_message is mapped to systemInstruction in standard Gemini format
    assert "systemInstruction" in payload
    assert payload["systemInstruction"]["parts"][0]["text"] == "Dev Message"


@patch("urllib.request.urlopen")
def test_call_gemini_with_metadata_openrouter_reasoning(mock_urlopen):
    """Verify that OpenRouter (chat-completions) uses 'developer' role for reasoning models."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "OpenRouter Reasoning Response"}
        }],
        "usage": {"prompt_tokens": 15, "completion_tokens": 25}
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    # Using OpenRouter key sk-or-...
    res = call_gemini_with_metadata(
        prompt="Hello",
        api_key="sk-or-mockkey",
        model="openai/o1-mini",
        temperature=0.7,
        developer_message="Dev Message",
        is_reasoning=True
    )
    assert res["text"] == "OpenRouter Reasoning Response"

    args, kwargs = mock_urlopen.call_args
    req = args[0]
    payload = json.loads(req.data.decode())

    # Verify messages structure for chat-completions
    assert "messages" in payload
    messages = payload["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "developer"
    assert messages[0]["content"] == "Dev Message"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello"
    assert "temperature" not in payload


def test_consensus_reasoning_prompting():
    """Verify consensus uses developer_message instead of prompt when model is reasoning."""
    adapter = GeminiExecutionAdapter()
    adapter.is_reasoning_model = MagicMock(return_value=True)
    adapter._call_gemini_and_track = MagicMock(return_value=json.dumps({"score": 8.5, "reason": "test reasoning"}))

    consensus = ConsensusEngine(
        initial_roster=["Backend Wizard"],
        adapter=adapter,
        model="o1-mini"
    )
    consensus.api_key = "mock-key"

    with patch("self_governance.consensus.get_persona") as mock_get_persona:
        mock_get_persona.return_value = {
            "role": "Backend Wizard",
            "division": "Engineering",
            "description": "Wizard desc",
            "prompt": "Standard XML Prompt",
            "developer_message": "Reasoning Dev Message"
        }
        
        consensus._score_agent("Backend Wizard", "peer feedback")
        
        # Verify that get_persona prompt checked uses developer_message
        args, kwargs = adapter._call_gemini_and_track.call_args
        prompt_passed = args[0]
        assert "Reasoning Dev Message" in prompt_passed
        assert "Standard XML Prompt" not in prompt_passed


# ===========================================================================
# 2. Dynamic Ability Loading Tests
# ===========================================================================

def test_ability_loader_scan_and_load(tmp_path):
    """Verify that AbilityLoader loads abilities and updates agent context."""
    abilities_dir = tmp_path / "abilities"
    abilities_dir.mkdir()
    
    # Create a dummy ability JSON file
    ability_data = {
        "name": "super_logging",
        "description": "Enables verbose logging.",
        "instructions": "Log every method call entry and exit with timestamps."
    }
    with open(abilities_dir / "super_logging.json", "w", encoding="utf-8") as f:
        json.dump(ability_data, f)

    # Create a dummy ability YAML file
    ability_yaml = {
        "name": "fast_cache",
        "description": "Enables redis cache.",
        "instructions": "Cache all database reads for up to 60 seconds."
    }
    with open(abilities_dir / "fast_cache.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(ability_yaml, f)

    loader = AbilityLoader(abilities_dir=str(abilities_dir))
    scanned = loader.scan_abilities()
    assert "super_logging" in scanned
    assert "fast_cache" in scanned

    # Test loading to Agent object
    agent = Agent(role="Coder", prompt="Agent Prompt", capabilities=[])
    success = loader.load_ability("super_logging", agent)
    assert success is True
    assert "super_logging" in agent.capabilities
    assert "Log every method call entry" in agent.prompt

    # Test loading to dict context
    ctx = {"prompt": "Dict Prompt", "capabilities": []}
    success = loader.load_ability("fast_cache", ctx)
    assert success is True
    assert "fast_cache" in ctx["capabilities"]
    assert "Cache all database reads" in ctx["prompt"]


# ===========================================================================
# 3. QA/Security Gatekeeper Logic Tests
# ===========================================================================

def test_fail_on_verify_config(tmp_path):
    """Verify that OrchestratorConfig parses fail_on_verify option."""
    config_yaml = {
        "watcher": {
            "fail_on_verify": False
        }
    }
    yaml_path = tmp_path / "config.yaml"
    with open(yaml_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config_yaml, f)

    config = OrchestratorConfig(config_path=str(yaml_path))
    assert config.fail_on_verify is False

    # Default should be True
    default_config = OrchestratorConfig()
    assert default_config.fail_on_verify is True


@patch("subprocess.run")
def test_gatekeeper_halt_on_failure(mock_run, tmp_path):
    """Verify that nudger halts succession and writes FAILED status when verify fails."""
    # Setup handoff file
    handoff_dir = tmp_path / ".planning"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = handoff_dir / "CURRENT_STATE.md"
    
    initial_content = "---\nstatus: \"COMPLETED\"\ncandidates: []\n---\n# Handoff content"
    with open(handoff_file, "w", encoding="utf-8") as f:
        f.write(initial_content)

    config = OrchestratorConfig()
    config.config_data["watcher"]["handoff_file"] = ".planning/CURRENT_STATE.md"
    
    # Mock subprocess.run to return non-zero exit code (pytest fails)
    mock_pytest_res = MagicMock()
    mock_pytest_res.returncode = 1
    mock_pytest_res.stdout = "Pytest failures traceback"
    
    mock_audit_res = MagicMock()
    mock_audit_res.returncode = 0
    mock_audit_res.stdout = ""
    
    mock_run.side_effect = [mock_pytest_res, mock_audit_res]

    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    nudger._execute_succession_safely = MagicMock(return_value=True)

    # Process handoff
    nudger.process_handoff()

    # Verify succession was not executed/called
    nudger._execute_succession_safely.assert_not_called()

    # Verify handoff file was updated to status: FAILED
    with open(handoff_file, "r", encoding="utf-8") as f:
        new_content = f.read()

    assert "status: FAILED" in new_content
    assert "Verification failures summary:" in new_content
    assert "Pytest failed with exit code 1." in new_content


# ===========================================================================
# 4. Bulk Import 350+ Agents Tests
# ===========================================================================

def test_agent_schema_validation():
    """Verify that schema validation functions correctly on valid/invalid inputs."""
    
    # Valid profile
    valid = {
        "sdlc": {
            "Test Wiz": {
                "role": "Test Wiz",
                "prompt": "Test prompt",
                "capabilities": ["magic"]
            }
        }
    }
    
    # Invalid profile (missing prompt)
    invalid = {
        "sdlc": {
            "Test Wiz": {
                "role": "Test Wiz",
                "capabilities": ["magic"]
            }
        }
    }

    # Validate valid profile
    from pydantic import BaseModel, Field
    from typing import List, Optional

    class ImportedAgentProfile(BaseModel):
        role: str
        prompt: str
        capabilities: List[str] = Field(default_factory=list)
        developer_message: Optional[str] = None

    # Valid profile should pass validation
    prof = ImportedAgentProfile.model_validate(valid["sdlc"]["Test Wiz"])
    assert prof.role == "Test Wiz"

    # Invalid profile should raise ValidationError
    with pytest.raises(ValidationError):
        ImportedAgentProfile.model_validate(invalid["sdlc"]["Test Wiz"])


def test_import_agents_cli(tmp_path):
    """Verify the import-agents CLI functionality via handler."""
    import_file = tmp_path / "import.json"
    agent_data = {
        "sdlc": {
            "New Agent": {
                "role": "New Agent",
                "prompt": "Injected prompt details",
                "capabilities": ["import-test"],
                "division": "Engineering",
                "emoji": "🤖",
                "vibe": "Test import vibe.",
                "description": "Test description."
            }
        }
    }
    with open(import_file, "w", encoding="utf-8") as f:
        json.dump(agent_data, f)

    from self_governance.cli import handle_import_agents
    args = MagicMock()
    args.file = str(import_file)

    # Patch the destination file path to a temp directory instead of the project assets
    dest_file = tmp_path / "agents.json"
    
    with patch("os.path.join", return_value=str(dest_file)):
        handle_import_agents(args)
        
    assert dest_file.exists()
    with open(dest_file, "r", encoding="utf-8") as f:
        imported = json.load(f)
        
    assert "New Agent" in imported["sdlc"]
    assert imported["sdlc"]["New Agent"]["prompt"] == "Injected prompt details"
