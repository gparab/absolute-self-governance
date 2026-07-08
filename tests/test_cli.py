import sys
import json
import pytest
from unittest.mock import patch, MagicMock
from self_governance.cli import main

def test_cli_dimension(capsys):
    test_args = ["self-governance", "dimension", "-r", "[2.0, 3.0]", "-m", "[[1.0, 0.0], [0.0, 1.0]]"]
    with patch.object(sys, "argv", test_args):
        main()
    
    captured = capsys.readouterr()
    output_json = json.loads(captured.out)
    
    assert "swarm" in output_json
    assert len(output_json["swarm"]) == 5
    assert output_json["swarm"][0]["role"] == "role_0"
    assert output_json["swarm"][2]["role"] == "role_1"

def test_cli_trigger_succession(tmp_path):
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n  - agent_B\n")
    
    test_args = [
        "self-governance",
        "trigger-succession",
        "--handoff",
        str(handoff_file),
        "--workdir",
        str(tmp_path)
    ]
    
    with patch.object(sys, "argv", test_args):
        main()
        
    prompt_file = tmp_path / "prompt_draft.md"
    log_file = tmp_path / "roster_rotation_log.md"
    
    assert prompt_file.exists()
    assert log_file.exists()
    
    log_content = log_file.read_text(encoding="utf-8")
    assert "Approved Roster" in log_content

def test_cli_run_nudger():
    test_args = ["self-governance", "run-nudger", "--workdir", "/dummy/path"]
    
    with patch("self_governance.cli.ContinuousNudger") as mock_nudger_class:
        mock_instance = MagicMock()
        mock_nudger_class.return_value = mock_instance
        
        with patch.object(sys, "argv", test_args):
            main()
            
        mock_nudger_class.assert_called_once()
        args, kwargs = mock_nudger_class.call_args
        assert kwargs["working_directory"] == "/dummy/path"
        assert "config" in kwargs
        mock_instance.watch_handoff.assert_called_once()

def test_cli_invalid_arguments():
    test_args = ["self-governance", "invalid-command"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0

def test_cli_stats(capsys, tmp_path):
    # Ensure stats prints headers successfully
    test_args = ["self-governance", "stats"]
    with patch.object(sys, "argv", test_args):
        main()
    captured = capsys.readouterr()
    assert "Self-Governing Software Factory Dashboard" in captured.out

