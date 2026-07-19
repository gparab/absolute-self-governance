import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from self_governance.mcp_server import mcp, dimension_swarm_tool


def test_dimension_swarm_tool_matches_demo_scenarios_directly():
    """Sanity check the underlying function against the same numbers
    self-governance demo prints (trivial task -> 1 agent, complex task ->
    9 agents), independent of the MCP transport."""
    trivial = dimension_swarm_tool([1.0, 0.0])
    assert trivial["team_size"] == 1
    assert trivial["role_counts"] == {"Backend Wizard": 1}

    complex_ = dimension_swarm_tool([3.0, 4.0])
    assert complex_["team_size"] == 9
    assert complex_["role_counts"] == {
        "Backend Wizard": 4,
        "QA Specialist": 2,
        "Security Auditor": 3,
    }


def test_dimension_swarm_tool_response_omits_full_persona_prompts():
    """The full injected persona prompt is verbose noise for a tool
    response -- only role names/counts should come back."""
    result = dimension_swarm_tool([2.0, 2.0])

    assert "roles" in result and "role_counts" in result and "team_size" in result
    assert result["roles"] == list(result["role_counts"].keys()) or set(
        result["roles"]
    ) == set(result["role_counts"].keys())
    assert "prompt" not in str(result.keys())


def test_dimension_swarm_tool_accepts_custom_transition_matrix():
    custom_matrix = [[1.0]]
    result = dimension_swarm_tool([5.0], transition_matrix=custom_matrix)

    assert result["team_size"] == 5
    assert result["role_counts"] == {"Backend Wizard": 5}


@pytest.mark.anyio
async def test_mcp_server_exposes_and_serves_the_tool_over_the_real_protocol():
    """End-to-end check through the actual MCP client/server session
    machinery (not just calling the Python function directly), so a
    regression in tool registration or (de)serialization is caught."""
    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        tools = await client.list_tools()
        assert "dimension_swarm_tool" in [t.name for t in tools.tools]

        result = await client.call_tool(
            "dimension_swarm_tool", {"requirement_vector": [3.0, 4.0]}
        )

        assert result.isError is not True
        assert result.structuredContent["result"]["team_size"] == 9
        assert result.structuredContent["result"]["role_counts"] == {
            "Backend Wizard": 4,
            "QA Specialist": 2,
            "Security Auditor": 3,
        }
