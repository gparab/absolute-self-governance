import os
import json
import logging
import pytest
import urllib.request
import urllib.error
import subprocess
from unittest.mock import MagicMock

from self_governance.models import Agent, SwarmConfig
from self_governance.telemetry import StructuredJSONFormatter, setup_telemetry
from self_governance.gemini_adapter import (
    GeminiExecutionAdapter,
    call_gemini_with_metadata,
)


def test_models_coverage_boost():
    # 1. Agent dict mapping coverage
    agent = Agent(role="Tester", prompt="Test Prompt", capabilities=["a"])

    # Contains
    assert "role" in agent
    assert "nonexistent" not in agent

    # Setitem & KeyError
    agent["role"] = "NewTester"
    assert agent.role == "NewTester"
    with pytest.raises(KeyError):
        agent["invalid"] = 1

    # Getitem KeyError
    with pytest.raises(KeyError):
        _ = agent["invalid"]

    # Delitem
    with pytest.raises(TypeError):
        del agent["role"]
    with pytest.raises(KeyError):
        del agent["invalid"]

    # Keys, values, items, iter, len
    assert agent.keys() == ["role", "prompt", "capabilities"]
    assert agent.values() == ["NewTester", "Test Prompt", ["a"]]
    assert agent.items() == [
        ("role", "NewTester"),
        ("prompt", "Test Prompt"),
        ("capabilities", ["a"]),
    ]
    assert list(agent) == ["role", "prompt", "capabilities"]
    assert len(agent) == 3
    assert agent.model_dump() == agent.dict()

    # 2. SwarmConfig dict mapping coverage
    config = SwarmConfig(swarm=[agent])

    # Getitem
    assert config["swarm"] == [agent]
    with pytest.raises(KeyError):
        _ = config["invalid"]

    # Setitem
    config["swarm"] = [agent, agent]
    assert len(config.swarm) == 2
    with pytest.raises(KeyError):
        config["invalid"] = 1

    # Delitem
    del config["swarm"]
    assert not hasattr(config, "swarm")
    with pytest.raises(KeyError):
        del config["invalid"]

    # Keys/values/items empty
    assert config.keys() == []
    assert config.values() == []
    assert config.items() == []

    # Re-initialize
    config.swarm = [agent]
    assert config.keys() == ["swarm"]
    assert config.values() == [[agent]]
    assert config.items() == [("swarm", [agent])]
    assert list(config) == ["swarm"]
    assert len(config) == 1

    # Delete and check Getattr AttributeError
    del config["swarm"]
    with pytest.raises(AttributeError):
        _ = config.swarm
    with pytest.raises(AttributeError):
        _ = config.nonexistent

    # Re-initialize and test contains
    config.swarm = [agent]
    assert "swarm" in config
    assert "nonexistent" not in config

    # Serialization limit > 1000
    large_swarm = SwarmConfig(swarm=[agent] * 1001)
    res_large = large_swarm.dict()
    assert len(res_large["swarm"]) == 1001
    assert isinstance(res_large["swarm"][0], Agent)

    empty_config = SwarmConfig(swarm=[])
    del empty_config.swarm
    assert empty_config.dict() == {}


def test_telemetry_coverage_boost(monkeypatch):
    # Exception formatting in StructuredJSONFormatter. A real LogRecord is
    # used (not MagicMock) because the formatter now does hasattr() checks
    # for known extra fields, and MagicMock fabricates a truthy attribute
    # for any name accessed, which broke this test with an unrelated field.
    formatter = StructuredJSONFormatter()
    try:
        raise ValueError("Simulated error")
    except ValueError:
        import sys

        exc_info = sys.exc_info()

    log_record = logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="test_file.py",
        lineno=1,
        msg="Test log message",
        args=(),
        exc_info=exc_info,
    )

    res = formatter.format(log_record)
    parsed = json.loads(res)
    assert "exception" in parsed
    assert "Simulated error" in parsed["exception"]

    # setup_telemetry with json_logging = True/False on non-testing environment
    monkeypatch.setenv("TESTING", "False")
    setup_telemetry(json_logging=True)
    setup_telemetry(json_logging=False)


def test_gemini_adapter_coverage_boost(monkeypatch):
    # call_gemini_with_metadata URL schema building
    mock_response = MagicMock()
    mock_response.read.return_value = json.dumps(
        {
            "candidates": [{"content": {"parts": [{"text": "Hello world"}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 20},
        }
    ).encode("utf-8")

    mock_urlopen = MagicMock()
    mock_urlopen.return_value.__enter__.return_value = mock_response
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)

    res = call_gemini_with_metadata(
        prompt="Hi",
        api_key="valid_key",
        response_schema={"type": "STRING"},
        response_mime_type="application/json",
    )
    assert res["text"] == "Hello world"
    assert res["prompt_tokens"] == 10

    # Test subprocess Exception paths in GeminiExecutionAdapter
    adapter = GeminiExecutionAdapter(api_key="test_key")

    # 1. review_code exception
    def mock_subprocess_run_fail(*args, **kwargs):
        raise OSError("Subprocess execution failed")

    monkeypatch.setattr(subprocess, "run", mock_subprocess_run_fail)

    res_review = adapter.review_code([], {})
    assert res_review["status"] == "failed"

    # 2. run_security_scan exception
    res_sec = adapter.run_security_scan([], {})
    assert res_sec["status"] == "failed"

    # 3. execute_tests local subprocess failure (OSError)
    res_test = adapter.execute_tests([], {}, test_target="mock_target.py")
    assert res_test["status"] == "failed"

    # 4. execute_tests local subprocess success (to cover line 428-429)
    mock_proc = MagicMock(returncode=0, stdout="All tests passed", stderr="")
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: mock_proc)
    res_test_ok = adapter.execute_tests([], {}, test_target="mock_target.py")
    assert res_test_ok["status"] == "completed"


def test_learning_exceptions_coverage_boost(monkeypatch):
    import self_governance.learning

    # Force json.load exception to hit line 17-18
    def mock_json_load_fail(*args):
        raise ValueError("corrupt json")

    monkeypatch.setattr(json, "load", mock_json_load_fail)
    # Create a dummy file so it exists
    with open(self_governance.learning.LEARNING_STATE_FILE, "w") as f:
        f.write("invalid json")
    try:
        state = self_governance.learning.get_learning_state()
        assert state["runs_completed"] == 0
    finally:
        if os.path.exists(self_governance.learning.LEARNING_STATE_FILE):
            os.remove(self_governance.learning.LEARNING_STATE_FILE)

    # Force write exception to hit line 34-35
    original_open = open

    def mock_open_write_fail(file, mode, *args, **kwargs):
        if "learning_state" in file and "w" in mode:
            raise IOError("Write failed")
        return original_open(file, mode, *args, **kwargs)

    monkeypatch.setattr("builtins.open", mock_open_write_fail)
    self_governance.learning.save_learning_state({})
