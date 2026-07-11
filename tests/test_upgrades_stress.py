import json
import pytest
from unittest.mock import MagicMock, patch
from concurrent.futures import ThreadPoolExecutor

from self_governance.models import Agent
from self_governance.gemini_adapter import GeminiExecutionAdapter, call_gemini_with_metadata
from self_governance.ability_loader import AbilityLoader
from self_governance.config import OrchestratorConfig
from self_governance.nudger import ContinuousNudger
from self_governance.cli import handle_import_agents

# ===========================================================================
# 1. Model-Aware LLM Adapters Stress Tests
# ===========================================================================

def test_is_reasoning_model_extended_detection():
    """Stress test the reasoning model detection with various formats and cases."""
    adapter = GeminiExecutionAdapter()
    
    # Check casing variations
    assert adapter.is_reasoning_model("O1-MINI") is True
    assert adapter.is_reasoning_model("o3-MiNi") is True
    assert adapter.is_reasoning_model("GEMINI-2.0-FLASH-THINKING-EXP") is True
    assert adapter.is_reasoning_model("r1-Reasoning-v2") is True
    
    # Check prefixes/suffixes and providers
    assert adapter.is_reasoning_model("openai/o1-preview") is True
    assert adapter.is_reasoning_model("openrouter/o3-mini") is True
    assert adapter.is_reasoning_model("thinking-model-2025") is True
    assert adapter.is_reasoning_model("anthropic/claude-3-5-sonnet:thinking") is True
    
    # Check non-reasoning models
    assert adapter.is_reasoning_model("gpt-4o") is False
    assert adapter.is_reasoning_model("gpt-4-turbo") is False
    assert adapter.is_reasoning_model("gemini-1.5-pro") is False
    assert adapter.is_reasoning_model("claude-3-5-sonnet") is False
    assert adapter.is_reasoning_model(None) is False
    assert adapter.is_reasoning_model("") is False

def test_is_reasoning_model_deepseek_r1():
    """Verify that deepseek-r1 is recognized as a reasoning model."""
    adapter = GeminiExecutionAdapter()
    assert adapter.is_reasoning_model("deepseek/deepseek-r1") is True


@patch("urllib.request.urlopen")
def test_call_gemini_with_metadata_direct_payload_reasoning_stress(mock_urlopen):
    """Stress test direct Gemini API formatting for reasoning models with different temperatures."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "Success Response"}]}
        }],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20}
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    # Even if temperature is passed, it should be stripped from generationConfig for reasoning models
    res = call_gemini_with_metadata(
        prompt="Execute task",
        api_key="direct-api-key",
        model="o1-preview",
        temperature=1.5,
        developer_message="Special dev prompt",
        is_reasoning=True
    )
    assert res["text"] == "Success Response"

    args, kwargs = mock_urlopen.call_args
    req = args[0]
    payload = json.loads(req.data.decode())

    # Assert temperature is omitted from generationConfig
    if "generationConfig" in payload:
        assert "temperature" not in payload["generationConfig"]

    # Assert developer_message is mapped to systemInstruction
    assert "systemInstruction" in payload
    assert payload["systemInstruction"]["parts"][0]["text"] == "Special dev prompt"


@patch("urllib.request.urlopen")
def test_call_gemini_with_metadata_openrouter_reasoning_stress(mock_urlopen):
    """Stress test OpenRouter API formatting for reasoning models with developer roles and stripped temperature."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "OpenRouter output"}
        }],
        "usage": {"prompt_tokens": 5, "completion_tokens": 10}
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    res = call_gemini_with_metadata(
        prompt="Run process",
        api_key="sk-or-somekey",
        model="openai/o1-mini",
        temperature=0.0,
        developer_message="OpenRouter reasoning prompt",
        is_reasoning=True
    )
    assert res["text"] == "OpenRouter output"

    args, kwargs = mock_urlopen.call_args
    req = args[0]
    payload = json.loads(req.data.decode())

    # Verify messages and role mapping
    assert "messages" in payload
    messages = payload["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == "developer"
    assert messages[0]["content"] == "OpenRouter reasoning prompt"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Run process"
    
    # Temperature must not be present in payload
    assert "temperature" not in payload


@patch("urllib.request.urlopen")
def test_call_gemini_with_metadata_non_reasoning_direct(mock_urlopen):
    """Verify non-reasoning models preserve temperature and system instructions correctly."""
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps({
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "Standard Output"}]}
        }],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 5}
    }).encode()
    mock_urlopen.return_value.__enter__.return_value = mock_response

    res = call_gemini_with_metadata(
        prompt="Standard request",
        api_key="direct-api-key",
        model="gemini-2.5-flash",
        temperature=0.8,
        system_instruction="Standard system instruction",
        is_reasoning=False
    )
    assert res["text"] == "Standard Output"

    args, kwargs = mock_urlopen.call_args
    req = args[0]
    payload = json.loads(req.data.decode())

    # Temperature should be present in generationConfig
    assert "generationConfig" in payload
    assert payload["generationConfig"]["temperature"] == 0.8

    # systemInstruction should be populated
    assert "systemInstruction" in payload
    assert payload["systemInstruction"]["parts"][0]["text"] == "Standard system instruction"


# ===========================================================================
# 2. Dynamic Ability Loading Stress Tests
# ===========================================================================

def test_ability_loader_sequential_same_ability(tmp_path):
    """Verify loading the same ability multiple times sequentially behaves as expected."""
    abilities_dir = tmp_path / "abilities"
    abilities_dir.mkdir()
    
    ability_data = {
        "name": "super_logging",
        "description": "Enables verbose logging.",
        "instructions": "Log every method call."
    }
    with open(abilities_dir / "super_logging.json", "w", encoding="utf-8") as f:
        json.dump(ability_data, f)

    loader = AbilityLoader(abilities_dir=str(abilities_dir))
    agent = Agent(role="Coder", prompt="Agent Prompt", capabilities=[])

    # First load
    success = loader.load_ability("super_logging", agent)
    assert success is True
    assert "super_logging" in agent.capabilities
    assert agent.prompt.count("Log every method call.") == 1

    # Second load (should not duplicate in prompt or capabilities)
    success2 = loader.load_ability("super_logging", agent)
    assert success2 is True
    assert len(agent.capabilities) == 1
    assert agent.capabilities == ["super_logging"]
    assert agent.prompt.count("Log every method call.") == 1


def test_ability_loader_missing_or_invalid(tmp_path):
    """Verify loading non-existent, invalid, or malformed abilities returns False."""
    abilities_dir = tmp_path / "abilities"
    abilities_dir.mkdir()
    
    loader = AbilityLoader(abilities_dir=str(abilities_dir))
    agent = Agent(role="Coder", prompt="Agent Prompt", capabilities=[])

    # Loading non-existent ability
    assert loader.load_ability("non_existent_ability", agent) is False

    # Loading case-insensitive name variations should resolve correctly
    ability_data = {
        "name": "sql_concurrency",
        "description": "SQL lock protection.",
        "instructions": "Lock table."
    }
    with open(abilities_dir / "sql_concurrency.json", "w", encoding="utf-8") as f:
        json.dump(ability_data, f)

    assert loader.load_ability("SQL_CONCURRENCY", agent) is True

def test_ability_loader_case_preservation_bug(tmp_path):
    """Verify that loading an ability with case-insensitive name results in canonical name in capabilities."""
    abilities_dir = tmp_path / "abilities"
    abilities_dir.mkdir()
    loader = AbilityLoader(abilities_dir=str(abilities_dir))
    agent = Agent(role="Coder", prompt="Agent Prompt", capabilities=[])

    ability_data = {
        "name": "sql_concurrency",
        "description": "SQL lock protection.",
        "instructions": "Lock table."
    }
    with open(abilities_dir / "sql_concurrency.json", "w", encoding="utf-8") as f:
        json.dump(ability_data, f)

    loader.load_ability("SQL_CONCURRENCY", agent)
    # The loader currently appends 'SQL_CONCURRENCY' instead of 'sql_concurrency'
    assert "sql_concurrency" in agent.capabilities


def test_ability_loader_concurrent_runs(tmp_path):
    """Verify ability loader behavior under concurrent sequential load operations on separate threads."""
    abilities_dir = tmp_path / "abilities"
    abilities_dir.mkdir()
    
    # Create 3 abilities
    abilities = {
        "ability_a": "Instructions A",
        "ability_b": "Instructions B",
        "ability_c": "Instructions C",
    }
    for name, inst in abilities.items():
        with open(abilities_dir / f"{name}.json", "w", encoding="utf-8") as f:
            json.dump({"name": name, "instructions": inst}, f)

    loader = AbilityLoader(abilities_dir=str(abilities_dir))

    # We will run concurrent operations on separate Agent instances
    agents = [Agent(role=f"Agent_{i}", prompt="Prompt", capabilities=[]) for i in range(10)]

    def run_loading(agent):
        for name in abilities:
            loader.load_ability(name, agent)

    with ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(run_loading, agents))

    # Verify all agents got all capabilities injected correctly without data races
    for agent in agents:
        assert sorted(agent.capabilities) == ["ability_a", "ability_b", "ability_c"]
        for inst in abilities.values():
            assert inst in agent.prompt


# ===========================================================================
# 3. QA/Security Gatekeeper Stress Tests
# ===========================================================================

def run_nudger_gatekeeper_scenario(tmp_path, fail_on_verify, pytest_exit, audit_exit):
    """Helper to run a nudger verification flow scenario and return results."""
    # Setup directories and files
    handoff_dir = tmp_path / ".planning"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = handoff_dir / "CURRENT_STATE.md"
    
    initial_content = "---\nstatus: \"COMPLETED\"\ncandidates: [\"Backend Wizard\"]\n---\n# Handoff content"
    with open(handoff_file, "w", encoding="utf-8") as f:
        f.write(initial_content)

    config = OrchestratorConfig()
    config.config_data["watcher"] = {
        "handoff_file": ".planning/CURRENT_STATE.md",
        "fail_on_verify": fail_on_verify
    }
    
    # Patch subprocess.run for pytest and security-audit
    mock_run = MagicMock()
    
    mock_pytest_res = MagicMock()
    mock_pytest_res.returncode = pytest_exit
    mock_pytest_res.stdout = "Pytest standard output log"
    
    mock_audit_res = MagicMock()
    mock_audit_res.returncode = audit_exit
    mock_audit_res.stdout = "Audit standard output log"
    
    mock_run.side_effect = [mock_pytest_res, mock_audit_res]

    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    
    # Mock succession trigger to record if it was called
    succession_called = False
    def mock_trigger(content, **kwargs):
        nonlocal succession_called
        succession_called = True
        return MagicMock()
    nudger.trigger_succession = mock_trigger

    with patch("subprocess.run", mock_run):
        nudger.process_handoff()

    # Read final handoff content
    with open(handoff_file, "r", encoding="utf-8") as f:
        final_content = f.read()

    return succession_called, final_content


def test_gatekeeper_pytest_failed_fail_on_verify_true(tmp_path):
    """Verify that nudger halts and sets status to FAILED when pytest fails and fail_on_verify is True."""
    succession_called, final_content = run_nudger_gatekeeper_scenario(
        tmp_path, fail_on_verify=True, pytest_exit=1, audit_exit=0
    )
    assert succession_called is False
    assert "status: FAILED" in final_content
    assert "Verification failures summary:" in final_content
    assert "Pytest failed with exit code 1." in final_content
    assert "Security audit failed" not in final_content


def test_gatekeeper_audit_failed_fail_on_verify_true(tmp_path):
    """Verify that nudger halts and sets status to FAILED when security audit fails and fail_on_verify is True."""
    succession_called, final_content = run_nudger_gatekeeper_scenario(
        tmp_path, fail_on_verify=True, pytest_exit=0, audit_exit=1
    )
    assert succession_called is False
    assert "status: FAILED" in final_content
    assert "Verification failures summary:" in final_content
    assert "Security audit failed with exit code 1." in final_content
    assert "Pytest failed" not in final_content


def test_gatekeeper_both_failed_fail_on_verify_true(tmp_path):
    """Verify that nudger halts and sets status to FAILED when both fail and fail_on_verify is True."""
    succession_called, final_content = run_nudger_gatekeeper_scenario(
        tmp_path, fail_on_verify=True, pytest_exit=2, audit_exit=1
    )
    assert succession_called is False
    assert "status: FAILED" in final_content
    assert "Verification failures summary:" in final_content
    assert "Pytest failed with exit code 2." in final_content
    assert "Security audit failed with exit code 1." in final_content


def test_gatekeeper_both_failed_fail_on_verify_false(tmp_path):
    """Verify that nudger proceeds and does NOT halt when both fail but fail_on_verify is False."""
    succession_called, final_content = run_nudger_gatekeeper_scenario(
        tmp_path, fail_on_verify=False, pytest_exit=1, audit_exit=1
    )
    # Since fail_on_verify is False, succession is executed despite failures
    assert succession_called is True
    # The status in the handoff should not be rewritten to FAILED
    assert "status: FAILED" not in final_content
    assert "status: \"COMPLETED\"" in final_content or "status: COMPLETED" in final_content


def test_gatekeeper_all_passed(tmp_path):
    """Verify that nudger proceeds successfully when both checks pass."""
    succession_called, final_content = run_nudger_gatekeeper_scenario(
        tmp_path, fail_on_verify=True, pytest_exit=0, audit_exit=0
    )
    assert succession_called is True
    assert "status: FAILED" not in final_content


def test_gatekeeper_exception_fail_on_verify_true(tmp_path):
    """Verify that nudger treats subprocess exception as verify failure and halts when fail_on_verify is True."""
    handoff_dir = tmp_path / ".planning"
    handoff_dir.mkdir(parents=True, exist_ok=True)
    handoff_file = handoff_dir / "CURRENT_STATE.md"
    
    initial_content = "---\nstatus: \"COMPLETED\"\ncandidates: [\"Backend Wizard\"]\n---\n# Handoff content"
    with open(handoff_file, "w", encoding="utf-8") as f:
        f.write(initial_content)

    config = OrchestratorConfig()
    config.config_data["watcher"] = {
        "handoff_file": ".planning/CURRENT_STATE.md",
        "fail_on_verify": True
    }
    
    mock_run = MagicMock()
    mock_run.side_effect = FileNotFoundError("Executable not found")

    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    
    succession_called = False
    def mock_trigger(content, **kwargs):
        nonlocal succession_called
        succession_called = True
        return MagicMock()
    nudger.trigger_succession = mock_trigger

    with patch("subprocess.run", mock_run):
        nudger.process_handoff()

    assert succession_called is False


# ===========================================================================
# 4. Bulk Agent Importer Stress Tests
# ===========================================================================

def test_bulk_import_missing_file():
    """Verify importing a non-existent file halts with exit code 1."""
    args = MagicMock()
    args.file = "non_existent_file.json"
    
    with pytest.raises(SystemExit) as excinfo:
        handle_import_agents(args)
    assert excinfo.value.code == 1


def test_bulk_import_empty_or_malformed_file(tmp_path):
    """Verify importing empty or malformed files raises error and exits."""
    # 1. Empty file
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("")
    
    args = MagicMock()
    args.file = str(empty_file)
    with pytest.raises(SystemExit) as excinfo:
        handle_import_agents(args)
    assert excinfo.value.code == 1

    # 2. Malformed JSON
    malformed_file = tmp_path / "malformed.json"
    malformed_file.write_text("{invalid json")
    
    args.file = str(malformed_file)
    with pytest.raises(SystemExit) as excinfo:
        handle_import_agents(args)
    assert excinfo.value.code == 1


def test_bulk_import_missing_required_keys(tmp_path):
    """Verify that importing json without required 'sdlc' or 'council' keys exits 1."""
    bad_schema_file = tmp_path / "bad_schema.json"
    # Lacks both 'sdlc' and 'council'
    bad_data = {
        "random_key": {
            "agent": {}
        }
    }
    with open(bad_schema_file, "w", encoding="utf-8") as f:
        json.dump(bad_data, f)
        
    args = MagicMock()
    args.file = str(bad_schema_file)
    with pytest.raises(SystemExit) as excinfo:
        handle_import_agents(args)
    assert excinfo.value.code == 1


def test_bulk_import_schema_formatting_validation_errors(tmp_path):
    """Verify that validation errors inside agent profiles successfully catch formatting errors."""
    import_file = tmp_path / "import_errors.json"
    agent_data = {
        "sdlc": {
            # Missing prompt
            "Agent No Prompt": {
                "role": "Agent No Prompt",
                "capabilities": ["test"]
            },
            # Invalid capabilities type
            "Agent Bad Caps": {
                "role": "Agent Bad Caps",
                "prompt": "Vibe prompt",
                "capabilities": "not-a-list"
            }
        }
    }
    with open(import_file, "w", encoding="utf-8") as f:
        json.dump(agent_data, f)

    args = MagicMock()
    args.file = str(import_file)
    
    # Verification of error count exit code 1
    with pytest.raises(SystemExit) as excinfo:
        handle_import_agents(args)
    assert excinfo.value.code == 1
