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
