"""SDLC subagent swarm dimensioning module.

Calculates the optimal swarm size and distribution of roles (e.g. Backend Wizard,
QA Specialist, Security Auditor) based on requirement complexity using transition
matrices and Shannon entropy scaling, returning a memory-efficient LazyList.
"""

import bisect
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Union, Iterator, overload
from collections.abc import Sequence
from self_governance.models import Agent, SwarmConfig


class LazyList(Sequence[Agent]):
    """A memory-efficient, immutable sequence implementation.

    Dynamically instantiates Agent objects on-demand using binary search over
    prefix sums rather than keeping them instantiated in memory.
    """

    def __init__(
        self,
        prefix_sums: List[int],
        total_count: int,
        capabilities: Optional[List[str]] = None,
    ) -> None:
        """Initializes the LazyList.

        Args:
            prefix_sums: Cumulative sums of agent counts per role to allow binary search.
            total_count: Total number of agents in the list.
            capabilities: Injected capability names.
        """
        self._prefix_sums = prefix_sums
        self._total_count = total_count
        self._capabilities = capabilities or []

    def __len__(self) -> int:
        """Returns the total number of agents.

        Returns:
            The total count of agents.
        """
        return self._total_count

    @overload
    def __getitem__(self, idx: int) -> Agent: ...

    @overload
    def __getitem__(self, idx: slice) -> List[Agent]: ...

    def __getitem__(self, idx: Union[int, slice]) -> Union[Agent, List[Agent]]:
        """Retrieves agent(s) at the given index or slice.

        Args:
            idx: An integer index or a slice object.

        Returns:
            An Agent or list of Agents matching the index or slice.

        Raises:
            TypeError: If the index is not an integer or slice.
            IndexError: If the index is out of bounds.
        """
        if isinstance(idx, slice):
            start, stop, step = idx.indices(self._total_count)
            return [self[i] for i in range(start, stop, step)]

        if not isinstance(idx, int):
            raise TypeError("Index must be an integer or slice")

        if idx < 0:
            idx += self._total_count

        if idx < 0 or idx >= self._total_count:
            raise IndexError("list index out of range")

        role_idx = bisect.bisect_right(self._prefix_sums, idx)
        # config.py's default webhook_matrix has 4 rows, but role_map only
        # covered 3 (peer-review batch, July 2026): the 4th row silently
        # fell through to role_map.get's f"role_{role_idx}" fallback, which
        # get_persona() (called with no adapter here, so it can't
        # LLM-synthesize a real one either) resolves to its generic
        # placeholder persona -- staffing every webhook-triggered swarm's
        # 4th slot with a dummy bot instead of a real specialist, since
        # this is the actual default production configuration path, not
        # an edge case.
        role_map = {
            0: "Backend Wizard",
            1: "QA Specialist",
            2: "Security Auditor",
            3: "DevOps Automator",
        }
        mapped_role = role_map.get(role_idx, f"role_{role_idx}")

        from self_governance.agency_agents_adapter import (
            get_persona,
            get_capability_prompt,
        )

        persona = get_persona(mapped_role)

        augmented_prompt = persona["prompt"]
        if self._capabilities:
            augmented_prompt += "\n\n### Injected Capabilities / Skills Guidelines:\n"
            for cap in self._capabilities:
                prompt_chunk = get_capability_prompt(cap)
                if prompt_chunk:
                    augmented_prompt += f"- {prompt_chunk}\n"

        return Agent(
            role=persona["role"],
            prompt=augmented_prompt,
            capabilities=self._capabilities,
        )

    def __iter__(self) -> Iterator[Agent]:
        """Iterate over all agents in the list.

        Yields:
            Agent: The next Agent instance in the sequence.
        """
        for i in range(self._total_count):
            yield self[i]


class AgentLifecycleStatus:
    IDLE = "idle"
    BUSY = "busy"
    FAILED = "failed"


@dataclass
class SwarmAgentState:
    """A spawned persona modeled as a resource object with a lifecycle
    (DRAMA, research.google survey, July 2026 topic-page batch): DRAMA
    separates a control plane (allocation/reassignment) from a worker plane
    (task execution) and models both agents and tasks as resource objects
    so the system tolerates an agent failing or a task's needs shifting
    mid-run without respawning the whole swarm.

    Deliberately not wired into dimension_swarm()'s return type -- that
    function's SwarmConfig/LazyList contract is unchanged; a caller that
    wants churn resilience tracks SwarmAgentState alongside the swarm it
    already dimensioned and calls reassign_failed_agent() when needed.
    """

    role: str
    status: str = AgentLifecycleStatus.IDLE
    affinity_tags: "tuple[str, ...]" = ()


def reassign_failed_agent(
    agents: List[SwarmAgentState], failed_index: int, required_tags: "tuple[str, ...]" = ()
) -> Optional[int]:
    """Finds the next-highest-affinity idle agent to take over for a failed
    one, instead of respawning the whole swarm.

    Affinity is scored as the count of required_tags the candidate's
    affinity_tags already cover -- a plain overlap count, not a learned
    model; ties break by lowest index (stable, deterministic).

    Args:
        agents: the current swarm's agent states.
        failed_index: index of the agent whose status is being set to FAILED.
        required_tags: tags the replacement should ideally cover.

    Returns:
        The index of the idle agent reassigned to take over, or None if no
        idle agent was available. Mutates agents in place: marks
        failed_index as FAILED and, if a replacement is found, marks it BUSY.
    """
    if failed_index < 0 or failed_index >= len(agents):
        raise IndexError("failed_index out of range")

    agents[failed_index].status = AgentLifecycleStatus.FAILED

    best_index: Optional[int] = None
    best_score = -1
    for i, agent in enumerate(agents):
        if i == failed_index or agent.status != AgentLifecycleStatus.IDLE:
            continue
        score = len(set(agent.affinity_tags) & set(required_tags))
        if score > best_score:
            best_score = score
            best_index = i

    if best_index is not None:
        agents[best_index].status = AgentLifecycleStatus.BUSY
    return best_index


def select_volunteer(bids: Dict[str, float], min_bid: float = 0.0) -> Optional[str]:
    """Blackboard-style volunteer task assignment (research.google survey,
    July 2026 topic-page batch, Tier 2): rather than a central allocator
    assigning a task by tag-overlap (see reassign_failed_agent), each
    candidate agent independently posts a bid -- its own confidence
    estimate for handling this specific task -- to a shared blackboard, and
    the highest bid above min_bid wins. Complements affinity-based
    reassignment: that's for replacing a failed agent from known tags,
    this is for initial assignment when agents can self-assess suitability
    better than a static tag-overlap score would.

    Not wired into dimension_swarm() or any dispatch path -- a caller
    collects bids (e.g. from each candidate persona's own confidence
    self-report) and calls this to pick a winner.

    Args:
        bids: mapping of agent identifier -> bid (confidence estimate,
            any comparable scale -- the caller defines what a bid means).
        min_bid: the bar a bid must clear to be eligible at all. A bid
            exactly equal to min_bid is eligible (inclusive floor).

    Returns:
        The identifier of the highest bidder among eligible bids, or None
        if no bid clears min_bid. Ties break by lexicographically smallest
        identifier -- deterministic, not first-inserted (dict iteration
        order isn't a meaningful tiebreak signal here).
    """
    eligible = {agent: bid for agent, bid in bids.items() if bid >= min_bid}
    if not eligible:
        return None
    best_bid = max(eligible.values())
    winners = [agent for agent, bid in eligible.items() if bid == best_bid]
    return min(winners)


def dimension_swarm(
    requirement_vector: List[float], transition_matrix: List[List[float]]
) -> SwarmConfig:
    """Compute the optimal subagent swarm configuration based on a dynamic scaling model.

    S_t = round(W * R_t * (1 + H(R_t))), where R_t is a requirement vector,
    W is a transition matrix, and H(R_t) is the Shannon entropy of requirements.

    Args:
        requirement_vector: Feature requirement vector of length N.
        transition_matrix: Transition matrix of shape (M, N).

    Returns:
        A SwarmConfig wrapping a LazyList of subagents.

    Raises:
        TypeError: If requirements or transition matrix are not lists or contain non-numeric types.
        ValueError: If lists are empty or lengths do not match.
    """
    # 1. Input validation
    if not isinstance(requirement_vector, list):
        raise TypeError("requirement_vector must be a list")
    if not isinstance(transition_matrix, list):
        raise TypeError("transition_matrix must be a list")

    if len(requirement_vector) == 0 or len(transition_matrix) == 0:
        raise ValueError("Inputs cannot be empty")

    for val in requirement_vector:
        if isinstance(val, bool):
            raise TypeError("requirement_vector elements must be numeric, not bool")
        if not isinstance(val, (int, float)):
            raise TypeError("requirement_vector elements must be numeric")
        if not math.isfinite(val):
            raise ValueError("requirement_vector elements must be finite")

    for row in transition_matrix:
        if not isinstance(row, list):
            raise TypeError("transition_matrix must be a 2D list (list of lists)")
        if len(row) != len(requirement_vector):
            raise ValueError(
                "Each row in transition_matrix must match requirement_vector's length"
            )
        for val in row:
            if isinstance(val, bool):
                raise TypeError("transition_matrix elements must be numeric, not bool")
            if not isinstance(val, (int, float)):
                raise TypeError("transition_matrix elements must be numeric")
            if not math.isfinite(val):
                raise ValueError("transition_matrix elements must be finite")

    # 2. Compute Shannon Entropy of requirement_vector
    total_req = sum(val for val in requirement_vector if val > 0.0)
    entropy = 0.0
    if total_req > 0.0:
        for val in requirement_vector:
            if val > 0.0:
                p = val / total_req
                entropy -= p * math.log2(p)

    # 3. Compute subagent counts with entropy scaling factor (1 + H(R_t))
    counts = []
    for row in transition_matrix:
        dot_product = sum(w * r for w, r in zip(row, requirement_vector))
        count = max(0.0, dot_product) * (1.0 + entropy)
        counts.append(round(count))

    # 3. Compute prefix sums for LazyList
    prefix_sums = []
    current_sum = 0
    for count in counts:
        current_sum += count
        prefix_sums.append(current_sum)

    # 4. Resolve capabilities from requirement_vector
    resolved_caps = []
    if len(requirement_vector) > 0 and requirement_vector[0] > 0.0:
        resolved_caps.append("sqlite_concurrency")
    if len(requirement_vector) > 1 and requirement_vector[1] > 0.0:
        resolved_caps.extend(["hmac_verification", "path_traversal_hardening"])
    if len(requirement_vector) > 2 and requirement_vector[2] > 0.0:
        resolved_caps.append("pytest_coverage")

    # 5. Check if we should bifurcate (Hierarchical Swarming)
    total_complexity = sum(counts)
    if total_complexity > 5 or entropy > 1.0:
        # Hierarchical scale triggered
        # For demonstration of Path C, we split the requirement vector into domains
        frontend_req = [requirement_vector[0], 0.0] + (requirement_vector[2:] if len(requirement_vector) > 2 else [])
        backend_req = [0.0, requirement_vector[1] if len(requirement_vector) > 1 else 0.0] + (requirement_vector[2:] if len(requirement_vector) > 2 else [])
        
        frontend_counts = [round(max(0.0, sum(w * r for w, r in zip(row, frontend_req))) * (1.0 + entropy)) for row in transition_matrix]
        backend_counts = [round(max(0.0, sum(w * r for w, r in zip(row, backend_req))) * (1.0 + entropy)) for row in transition_matrix]

        
        fe_prefix, fe_sum = [], 0
        for c in frontend_counts:
            fe_sum += c
            fe_prefix.append(fe_sum)
            
        be_prefix, be_sum = [], 0
        for c in backend_counts:
            be_sum += c
            be_prefix.append(be_sum)
            
        return SwarmConfig(LazyList(prefix_sums, current_sum, capabilities=resolved_caps), hierarchical_swarms={
            "frontend": SwarmConfig(LazyList(fe_prefix, fe_sum, capabilities=resolved_caps)),
            "backend": SwarmConfig(LazyList(be_prefix, be_sum, capabilities=resolved_caps))
        })

    # 6. Return single SwarmConfig wrapping LazyList
    lazy_swarm = LazyList(prefix_sums, current_sum, capabilities=resolved_caps)
    return SwarmConfig(lazy_swarm)

