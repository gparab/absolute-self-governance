import sys
from unittest.mock import patch
from self_governance.benchmark import run_benchmark, load_benchmark_tasks
from self_governance.cli import main

def test_load_benchmark_tasks():
    tasks = load_benchmark_tasks()
    assert len(tasks) == 2
    assert tasks[0]["id"] == "task_secure_reader"

def test_run_benchmark_mocked(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    # Mock execute_development & execute_tests to return clean results
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_development", 
                        lambda self, agents, plan: {"status": "completed", "written_files": []})
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_tests", 
                        lambda self, agents, changes, test_target=None: {"status": "completed"})
    
    results = run_benchmark(api_key=None)
    assert "task_secure_reader" in results
    assert results["task_secure_reader"]["baseline"]["passed"] is True
    assert results["task_secure_reader"]["asg"]["passed"] is True

def test_cli_benchmark(monkeypatch, capsys):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_development", 
                        lambda self, agents, plan: {"status": "completed", "written_files": []})
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_tests", 
                        lambda self, agents, changes, test_target=None: {"status": "completed"})
    
    test_args = ["self-governance", "benchmark"]
    with patch.object(sys, "argv", test_args):
        main()
        
    captured = capsys.readouterr()
    assert "Secure File Reader" in captured.out
    assert "Thread Safe Cache" in captured.out
