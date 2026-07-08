from self_governance.execution import dispatch_swarm_execution
from self_governance.models import Agent


def test_dispatch_swarm_execution():
    agents = [
        Agent(role="dev", prompt="write code"),
        Agent(role="qa", prompt="run tests"),
        Agent(role="role_0", prompt="placeholder"),
    ]

    res = dispatch_swarm_execution(agents, "Implement OAuth flow")
    assert res["task"] == "Implement OAuth flow"
    assert res["agent_count"] == 3
    assert len(res["traces"]) == 3
    assert res["traces"][0]["agent_role"] == "dev"
    assert res["traces"][0]["status"] == "completed"
    assert "implemented" in res["traces"][0]["output"].lower()

    assert res["traces"][1]["agent_role"] == "qa"
    assert "verify" in res["traces"][1]["output"].lower()
