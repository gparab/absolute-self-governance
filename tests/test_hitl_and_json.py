import os
import json
import tempfile
import pytest
import yaml
from unittest.mock import MagicMock

from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.config import OrchestratorConfig
from self_governance.nudger import ContinuousNudger

def test_gemini_adapter_structured_json_parsing(tmp_path, monkeypatch):
    # Mock call_gemini to return a clean structured JSON
    json_payload = {
        "explanation": "Updated authentication methods",
        "written_files": [
            {
                "filepath": os.path.join(str(tmp_path), "auth_impl.py"),
                "content": "def authenticate():\n    return True\n"
            }
        ]
    }
    
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key, response_schema=None, response_mime_type=None: json.dumps(json_payload))
    
    adapter = GeminiExecutionAdapter(api_key="test_key")
    res = adapter.execute_development([], {"task": "Structured JSON Test"})
    
    assert res["status"] == "completed"
    assert "auth_impl.py" in res["written_files"][0]
    
    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def authenticate():" in content

def test_gemini_adapter_json_fallback(tmp_path, monkeypatch):
    # Mock call_gemini to return malformed JSON that fails loads, falling back to legacy parsing
    legacy_text = (
        "This is not JSON text.\n"
        "### WRITE_FILE: " + os.path.join(str(tmp_path), "fallback_impl.py") + "\n"
        "```python\n"
        "def fallback():\n"
        "    return 'fallback'\n"
        "```"
    )
    monkeypatch.setattr("self_governance.gemini_adapter.call_gemini", lambda prompt, key, response_schema=None, response_mime_type=None: legacy_text)
    
    adapter = GeminiExecutionAdapter(api_key="test_key")
    res = adapter.execute_development([], {"task": "Fallback Test"})
    
    assert res["status"] == "completed"
    assert "fallback_impl.py" in res["written_files"][0]
    
    with open(res["written_files"][0], "r", encoding="utf-8") as f:
        content = f.read()
    assert "def fallback():" in content

def test_nudger_dry_run_approval_flow(tmp_path, monkeypatch):
    # Mock run_consensus to avoid actual model execution during test
    mock_consensus = MagicMock()
    mock_consensus.return_value.approved_roster = ["Backend Wizard"]
    mock_consensus.return_value.prompt_tokens = 0
    mock_consensus.return_value.completion_tokens = 0
    monkeypatch.setattr("self_governance.nudger.run_consensus", mock_consensus)

    config = OrchestratorConfig()
    # Explicitly configure dry_run to True
    config.config_data["watcher"]["dry_run"] = True
    
    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    
    # 1. Create a handoff file with COMPLETED status
    handoff_path = os.path.join(str(tmp_path), "handoff.md")
    handoff_data = {
        "status": "COMPLETED",
        "candidates": ["Backend Wizard"]
    }
    with open(handoff_path, "w", encoding="utf-8") as f:
        yaml.dump(handoff_data, f)
        
    # Process handoff - should create dry_run_plan.json and wait
    nudger.process_handoff()
    
    dry_run_path = os.path.join(str(tmp_path), "dry_run_plan.json")
    assert os.path.exists(dry_run_path)
    
    with open(dry_run_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    assert plan["status"] == "AWAITING_APPROVAL"
    assert "Backend Wizard" in plan["swarm_counts"]
    
    # Consensus should not have been called yet
    assert not mock_consensus.called
    
    # 2. Approve via handoff.md status
    handoff_data["status"] = "APPROVED"
    with open(handoff_path, "w", encoding="utf-8") as f:
        yaml.dump(handoff_data, f)
        
    nudger.process_handoff()
    
    # Consensus should have run, logs updated, and dry_run_plan.json removed
    assert mock_consensus.called
    assert not os.path.exists(dry_run_path)
    
    # Roster log check
    roster_log = os.path.join(str(tmp_path), "roster_rotation_log.md")
    assert os.path.exists(roster_log)
    with open(roster_log, "r", encoding="utf-8") as f:
        log_content = f.read()
    assert "Succession Session Completed" in log_content
