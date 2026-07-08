import os
import time
import pytest
from self_governance.base_adapter import BaseExecutionAdapter
from self_governance.execution import MockExecutionAdapter, dispatch_swarm_execution
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.models import Agent

def test_adapter_inheritance():
    mock_adapter = MockExecutionAdapter()
    gemini_adapter = GeminiExecutionAdapter()
    
    assert isinstance(mock_adapter, BaseExecutionAdapter)
    assert isinstance(gemini_adapter, BaseExecutionAdapter)

def test_mock_adapter_pipeline():
    mock_adapter = MockExecutionAdapter()
    plan = mock_adapter.plan_task("Resolve issue #1")
    assert "task" in plan
    assert "steps" in plan
    
    dev_res = mock_adapter.execute_development([], plan)
    assert dev_res["status"] == "completed"

def test_gemini_adapter_fallback():
    # Without an API key, it uses fallback behavior without crashing
    gemini_adapter = GeminiExecutionAdapter(api_key=None)
    plan = gemini_adapter.plan_task("Speed up LazyList")
    assert "Fallback" in plan["steps"][0]

def test_dispatch_with_custom_adapter():
    mock_adapter = MockExecutionAdapter()
    agents = [Agent(role="dev", prompt="write code")]
    
    res = dispatch_swarm_execution(agents, "Task description", adapter=mock_adapter)
    assert res["task"] == "Task description"
    assert "plan" in res
    assert "security" in res
    assert "documentation" in res

def test_call_gemini_retry_backoff(monkeypatch):
    import urllib.request
    import urllib.error
    from unittest.mock import MagicMock
    from self_governance.gemini_adapter import call_gemini
    
    attempts = []
    
    def mock_urlopen(req, timeout=15):
        attempts.append(req)
        if len(attempts) == 1:
            # First try raises HTTPError 429
            fp = MagicMock()
            fp.read.return_value = b"Rate limit exceeded"
            raise urllib.error.HTTPError(req.full_url, 429, "Too Many Requests", req.headers, fp)
        else:
            # Second try returns valid content
            resp = MagicMock()
            resp.read.return_value = b'{"candidates": [{"content": {"parts": [{"text": "Mock response"}]}}]}'
            resp.__enter__.return_value = resp
            return resp
            
    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(time, "sleep", lambda x: None) # Fast sleep bypass
    
    result = call_gemini("Test prompt", "test_key")
    assert result == "Mock response"
    assert len(attempts) == 2

def test_gemini_execute_development_writes_file(tmp_path, monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    # Mock call_gemini to return a formatted code payload
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: (
        "Some thoughts before code.\n"
        "### WRITE_FILE: " + os.path.join(str(tmp_path), "swarm_generated.py") + "\n"
        "```python\n"
        "def generated_func():\n"
        "    return 42\n"
        "```"
    ))
    
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Write test func"})
    assert res["status"] == "completed"
    assert "swarm_generated.py" in res["written_files"][0]
    
    # Check that file was actually written to disk
    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def generated_func():" in content

def test_gemini_execute_tests_subprocess(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    import subprocess
    from unittest.mock import MagicMock
    
    mock_run = MagicMock()
    mock_run.return_value.returncode = 0
    mock_run.return_value.stdout = "104 passed in 0.50s"
    mock_run.return_value.stderr = ""
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    adapter = GeminiExecutionAdapter(api_key=None)
    res = adapter.execute_tests([], {})
    assert res["status"] == "completed"
    assert "passed" in res["output"]

def test_gemini_path_traversal_blocked(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    # Mock call_gemini to return a path traversal write attempt payload
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: (
        "### WRITE_FILE: ../../../etc/passwd\n"
        "```\n"
        "root:x:0:0:root:/root:/bin/bash\n"
        "```"
    ))
    
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Write malformed path"})
    assert res["status"] == "completed"
    # Ensure no files were written
    assert len(res["written_files"]) == 0

def test_path_traversal_prefix_bug(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    base_dir = os.path.abspath(".")
    malicious_path = base_dir + "-malicious/escaped.py"
    
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini_with_metadata", lambda prompt, key: {
        "text": "### WRITE_FILE: " + malicious_path + "\n```\nmalicious_code()\n```",
        "prompt_tokens": 100,
        "completion_tokens": 50
    })
    
    monkeypatch.setenv("TESTING", "False")
    
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Verify prefix bypass"})
    assert res["status"] == "completed"
    assert len(res["written_files"]) == 0

def test_gemini_empty_response(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: "")
    
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Expect failure status"})
    assert res["status"] == "failed"
    assert "Failed" in res["output"]

def test_gemini_parser_fuzzing(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    # 1. Test malformed code fences
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: (
        "### WRITE_FILE: test_malformed_fence.py\n"
        "``\n" # Missing third backtick
        "def bad(): pass\n"
        "``"
    ))
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Malformed fence test"})
    assert res["status"] == "completed"
    assert len(res["written_files"]) == 0

    # 2. Test multi-nested code blocks
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: (
        "### WRITE_FILE: test_nested.py\n"
        "```python\n"
        "def nested():\n"
        "    \"\"\"Nested block test\"\"\"\n"
        "    ```nested markdown```\n"
        "```"
    ))
    res = adapter.execute_development([], {"task": "Nested fence test"})
    assert res["status"] == "completed"
    assert len(res["written_files"]) == 1
    with open("test_nested.py", "r", encoding="utf-8") as f:
        content = f.read()
    assert "```nested markdown```" in content
    try:
        os.remove("test_nested.py")
    except Exception:
        pass

    # 3. Test huge payloads stream
    huge_code = "def large_func():\n" + "    pass\n" * 5000
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key: (
        "### WRITE_FILE: test_huge.py\n"
        "```python\n" + huge_code + "```"
    ))
    res = adapter.execute_development([], {"task": "Huge payload test"})
    assert res["status"] == "completed"
    assert len(res["written_files"]) == 1
    try:
        os.remove("test_huge.py")
    except Exception:
        pass




