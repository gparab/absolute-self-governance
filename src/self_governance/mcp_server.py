"""MCP server exposing ASG's dynamic swarm-sizing math as a tool any MCP
client (Claude Desktop, Claude Code, Cursor, etc.) can call directly.

Deliberately scoped to the one capability that's safe to expose with zero
gating: dimension_swarm() is a pure computation over floats -- no LLM call,
no API key, no file writes, no network access, no PolicyEngine-relevant
action. It's the same math `self-governance demo`/`dimension` already run.
Consensus, procedural memory, and anything that spends money or touches
disk are deliberately NOT exposed here; wiring those in would need the
same PolicyEngine gating the CLI's own dangerous actions go through, which
is a separate, bigger piece of work.
"""

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from self_governance.dimensioning import dimension_swarm

# Same matrix demo.py uses: tuned for a clear, illustrative before/after
# team-sizing story (scope, risk) -> (Backend Wizard, QA Specialist,
# Security Auditor), rather than mirroring the production webhook_matrix.
_DEFAULT_MATRIX = [
    [0.6, 0.0],
    [0.05, 0.25],
    [0.0, 0.35],
]

mcp = FastMCP("absolute-self-governance")


@mcp.tool()
def dimension_swarm_tool(
    requirement_vector: List[float],
    transition_matrix: Optional[List[List[float]]] = None,
) -> Dict[str, Any]:
    """Computes ASG's dynamic swarm-sizing math for a task's requirement
    vector -- no LLM call, no API key, no cost.

    Args:
        requirement_vector: Feature requirement floats, e.g. [scope, risk].
            Higher values staff a larger team.
        transition_matrix: Optional (role x requirement) matrix. Defaults
            to a 3-role (Backend Wizard, QA Specialist, Security Auditor)
            matrix tuned for a clear scope/risk story if omitted.

    Returns:
        {"roles": [str, ...], "team_size": int, "role_counts": {role: count}}
        -- roles omits each agent's full injected persona prompt (verbose,
        not useful to a tool caller); use the CLI's `dimension` subcommand
        for the full per-agent detail.
    """
    matrix = transition_matrix if transition_matrix is not None else _DEFAULT_MATRIX
    config = dimension_swarm(requirement_vector, matrix)
    roles = [agent.role for agent in config.swarm]
    role_counts: Dict[str, int] = {}
    for role in roles:
        role_counts[role] = role_counts.get(role, 0) + 1
    return {"roles": roles, "team_size": len(roles), "role_counts": role_counts}


def main() -> None:
    """Entry point for `self-governance mcp-server` -- runs the MCP server
    over stdio, the transport MCP clients (Claude Desktop, Claude Code,
    etc.) expect for a locally-configured tool server."""
    mcp.run()


if __name__ == "__main__":
    main()
