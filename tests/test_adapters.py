import os
import time
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.models import SessionStatus


def test_gemini_adapter_fallback():
    # Without an API key, it uses fallback behavior without crashing
    gemini_adapter = GeminiExecutionAdapter(api_key=None)
    plan = gemini_adapter.plan_task("Speed up LazyList")
    assert "Fallback" in plan["steps"][0]


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
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", req.headers, fp
            )
        else:
            # Second try returns valid content
            resp = MagicMock()
            resp.read.return_value = (
                b'{"candidates": [{"content": {"parts": [{"text": "Mock response"}]}}]}'
            )
            resp.__enter__.return_value = resp
            return resp

    monkeypatch.setattr(urllib.request, "urlopen", mock_urlopen)
    monkeypatch.setattr(time, "sleep", lambda x: None)  # Fast sleep bypass

    result = call_gemini("Test prompt", "test_key")
    assert result == "Mock response"
    assert len(attempts) == 2


def test_gemini_execute_development_writes_file(tmp_path, monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Mock call_gemini to return a formatted code payload
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "Some thoughts before code.\n"
            "### WRITE_FILE: "
            + os.path.join(str(tmp_path), "swarm_generated.py")
            + "\n"
            "```python\n"
            "def generated_func():\n"
            "    return 42\n"
            "```"
        ),
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Write test func"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert "swarm_generated.py" in res["written_files"][0]

    # Check that file was actually written to disk
    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def generated_func():" in content


def test_gemini_execute_development_blocks_writes_to_protected_paths(tmp_path, monkeypatch):
    """Disjoint write-scope (Agent-Loop-Skills' pattern): a plan can declare
    paths the generating agent must not write to -- e.g. the acceptance test
    file -- so a specialist persona can't make its own attempt pass by
    rewriting the test it's being judged against."""
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    protected_file = os.path.join(str(tmp_path), "protected_test.py")
    allowed_file = os.path.join(str(tmp_path), "impl.py")
    with open(protected_file, "w", encoding="utf-8") as f:
        f.write("# original test content\n")

    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "### WRITE_FILE: " + protected_file + "\n"
            "```python\n"
            "# malicious overwrite\n"
            "```\n"
            "### WRITE_FILE: " + allowed_file + "\n"
            "```python\n"
            "def impl():\n"
            "    return 1\n"
            "```"
        ),
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development(
        [], {"task": "Rewrite", "protected_write_paths": [protected_file]}
    )

    assert allowed_file in res["written_files"]
    assert protected_file not in res["written_files"]
    with open(protected_file, "r", encoding="utf-8") as f:
        assert f.read() == "# original test content\n"


def test_gemini_execute_development_applies_trust_and_depth_framing(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    captured_prompts = []

    def fake_call_gemini(prompt, key):
        captured_prompts.append(prompt)
        return '{"explanation": "done", "written_files": []}'

    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", fake_call_gemini)

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    adapter.execute_development([], {"task": "Write test func"})

    assert len(captured_prompts) == 1
    assert "trusted, capable engineer with full autonomy" in captured_prompts[0]
    assert "document the root cause" in captured_prompts[0]


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
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert "passed" in res["output"]


def test_gemini_path_traversal_blocked(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Mock call_gemini to return a path traversal write attempt payload
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "### WRITE_FILE: ../../../etc/passwd\n"
            "```\n"
            "root:x:0:0:root:/root:/bin/bash\n"
            "```"
        ),
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Write malformed path"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    # Ensure no files were written
    assert len(res["written_files"]) == 0


def test_path_traversal_prefix_bug(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    base_dir = os.path.abspath(".")
    malicious_path = base_dir + "-malicious/escaped.py"

    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini_with_metadata",
        lambda prompt, key: {
            "text": "### WRITE_FILE: "
            + malicious_path
            + "\n```\nmalicious_code()\n```",
            "prompt_tokens": 100,
            "completion_tokens": 50,
        },
    )

    monkeypatch.setenv("TESTING", "False")

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Verify prefix bypass"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert len(res["written_files"]) == 0


def test_gemini_empty_response(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini", lambda prompt, key: ""
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Expect failure status"})
    assert res["status"] == SessionStatus.FAILED.value.lower()
    assert "Failed" in res["output"]


def test_gemini_parser_fuzzing(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # 1. Test malformed code fences
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "### WRITE_FILE: test_malformed_fence.py\n"
            "``\n"  # Missing third backtick
            "def bad(): pass\n"
            "``"
        ),
    )
    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Malformed fence test"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert len(res["written_files"]) == 0

    # 2. Test multi-nested code blocks
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "### WRITE_FILE: test_nested.py\n"
            "```python\n"
            "def nested():\n"
            '    """Nested block test"""\n'
            "    ```nested markdown```\n"
            "```"
        ),
    )
    res = adapter.execute_development([], {"task": "Nested fence test"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
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
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "### WRITE_FILE: test_huge.py\n```python\n" + huge_code + "```"
        ),
    )
    res = adapter.execute_development([], {"task": "Huge payload test"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert len(res["written_files"]) == 1
    try:
        os.remove("test_huge.py")
    except Exception:
        pass


def test_model_routing(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    from self_governance.config import OrchestratorConfig

    called_models = []

    def mock_call_gemini_with_metadata(
        prompt, api_key, response_schema=None, response_mime_type=None, model=None
    ):
        called_models.append(model)
        return {"text": "{}", "prompt_tokens": 10, "completion_tokens": 10}

    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini_with_metadata",
        mock_call_gemini_with_metadata,
    )

    # Force dynamic configuration overrides
    config = OrchestratorConfig()
    config.config_data["models"] = {
        "default": "gemini-2.5-flash",
        "development": "gemini-custom-dev",
        "review": "gemini-custom-review",
        "security": "gemini-custom-security",
    }
    # Temporarily force get_learning_state or similar if needed, but OrchestratorConfig reads from its own config_data.
    # We mock OrchestratorConfig instantiation:
    monkeypatch.setattr(
        "self_governance.config.OrchestratorConfig", lambda *args, **kwargs: config
    )

    adapter = GeminiExecutionAdapter(api_key="test_key")
    assert adapter.model_development == "gemini-custom-dev"
    assert adapter.model_review == "gemini-custom-review"
    assert adapter.model_security == "gemini-custom-security"

    # Trigger development
    monkeypatch.setattr(
        os, "getenv", lambda name: "True" if name == "TESTING" else None
    )
    adapter.plan_task("Test task")
    assert "gemini-custom-dev" in called_models

    called_models.clear()
    adapter.review_code([], {})
    assert "gemini-custom-review" in called_models

    called_models.clear()
    adapter.run_security_scan([], {})
    assert "gemini-custom-security" in called_models


def test_gemini_execute_development_json_format(tmp_path, monkeypatch):
    import json
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Mock call_gemini to return a structured JSON code payload
    target_file = os.path.join(str(tmp_path), "swarm_generated_json.py")
    json_payload = {
        "explanation": "Implemented structured json generation",
        "written_files": [
            {
                "filepath": target_file,
                "content": "def json_func():\n    return 'json_format'\n"
            }
        ]
    }

    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: json.dumps(json_payload),
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Write json test"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert target_file in res["written_files"][0]

    # Check that file was actually written to disk
    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def json_func():" in content


def test_gemini_execute_development_json_fallback(tmp_path, monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    # Return a payload that is NOT valid JSON but contains legacy WRITE_FILE pattern
    target_file = os.path.join(str(tmp_path), "swarm_fallback.py")
    monkeypatch.setattr(
        "self_governance.gemini_adapter.call_gemini",
        lambda prompt, key: (
            "{invalid_json: true}\n"
            "### WRITE_FILE: " + target_file + "\n"
            "```python\n"
            "def fallback_func():\n"
            "    return 'fallback'\n"
            "```"
        ),
    )

    adapter = GeminiExecutionAdapter(api_key="valid_key")
    res = adapter.execute_development([], {"task": "Fallback test"})
    assert res["status"] == SessionStatus.COMPLETED.value.lower()
    assert target_file in res["written_files"][0]

    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def fallback_func():" in content

def test_gemini_adapter_config_path_wired(monkeypatch, tmp_path):
    """P1.1: Verify config.yaml model settings reach outgoing request."""
    import yaml
    from self_governance.gemini_adapter import GeminiExecutionAdapter

    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump({"models": {"default": "gemini-test-custom-1.0", "development": "gemini-test-custom-1.0"}}))

    adapter = GeminiExecutionAdapter(api_key="valid", config_path=str(config_file))
    
    # Mock execute_request in providers to capture payload
    captured_model = []
    
    def mock_execute(url, headers, data, parser):
        captured_model.append(data.get("model", "not-found")) # For openrouter
        if "generativelanguage" in url:
            captured_model.append(url)
        return {"text": "Success", "prompt_tokens": 1, "completion_tokens": 1, "finish_reason": "STOP"}
        
    import self_governance.providers
    monkeypatch.setattr(self_governance.providers, "_execute_request", mock_execute)
    
    adapter.plan_task("Some simple task")
    assert any("gemini-test-custom-1.0" in url for url in captured_model), f"Model not routed correctly: {captured_model}"
