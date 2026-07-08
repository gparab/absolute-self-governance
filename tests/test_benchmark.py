import sys
import os
import pytest
from unittest.mock import patch, MagicMock
from self_governance.benchmark import run_benchmark, load_benchmark_tasks
from self_governance.cli import main

def test_load_benchmark_tasks():
    tasks = load_benchmark_tasks()
    assert len(tasks) == 3
    assert tasks[0]["id"] == "task_palindrome"

def test_run_benchmark_mocked(monkeypatch):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    # Mock execute_development & execute_tests to return clean results
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_development", 
                        lambda self, agents, plan: {"status": "completed", "written_files": []})
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_tests", 
                        lambda self, agents, changes: {"status": "completed"})
    
    results = run_benchmark(api_key=None)
    assert "task_palindrome" in results
    assert results["task_palindrome"]["baseline"]["passed"] is True
    assert results["task_palindrome"]["asg"]["passed"] is True

def test_cli_benchmark(monkeypatch, capsys):
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_development", 
                        lambda self, agents, plan: {"status": "completed", "written_files": []})
    monkeypatch.setattr(GeminiExecutionAdapter, "execute_tests", 
                        lambda self, agents, changes: {"status": "completed"})
    
    test_args = ["self-governance", "benchmark"]
    with patch.object(sys, "argv", test_args):
        main()
        
    captured = capsys.readouterr()
    assert "Palindrome Validation" in captured.out
    assert "Memoized Fibonacci" in captured.out
    assert "Safe Division" in captured.out
