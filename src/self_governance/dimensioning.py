import bisect
import math
from typing import List, Union, Iterator
from self_governance.models import Agent, SwarmConfig

class LazyList(list):
    """
    A memory-efficient list implementation that dynamically instantiates
    Agent objects on-demand rather than keeping them in memory.
    """
    def __init__(self, prefix_sums: List[int], total_count: int) -> None:
        """
        Initialize LazyList.

        Args:
            prefix_sums: Cumulative sums of agent counts per role to allow binary search.
            total_count: Total number of agents in the list.
        """
        super().__init__()
        self._prefix_sums = prefix_sums
        self._total_count = total_count

    def __len__(self) -> int:
        """Return the total number of agents."""
        return self._total_count

    def __getitem__(self, idx: Union[int, slice]) -> Union[Agent, List[Agent]]:
        """
        Retrieve agent(s) at the given index or slice.

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
        return Agent(role=f"role_{role_idx}", prompt=f"Prompt for role_{role_idx}")

    def __iter__(self) -> Iterator[Agent]:
        """Iterate over all agents in the list."""
        for i in range(self._total_count):
            yield self[i]


def dimension_swarm(requirement_vector: List[float], transition_matrix: List[List[float]]) -> SwarmConfig:
    """
    Compute the optimal subagent swarm configuration based on a dynamic scaling model.

    S_t = round(W * R_t), where R_t is a requirement vector and W is a transition matrix.

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
            raise ValueError("Each row in transition_matrix must match requirement_vector's length")
        for val in row:
            if isinstance(val, bool):
                raise TypeError("transition_matrix elements must be numeric, not bool")
            if not isinstance(val, (int, float)):
                raise TypeError("transition_matrix elements must be numeric")
            if not math.isfinite(val):
                raise ValueError("transition_matrix elements must be finite")

    # 2. Compute subagent counts
    counts = []
    for row in transition_matrix:
        dot_product = sum(w * r for w, r in zip(row, requirement_vector))
        count = max(0.0, dot_product)
        counts.append(round(count))

    # 3. Compute prefix sums for LazyList
    prefix_sums = []
    current_sum = 0
    for count in counts:
        current_sum += count
        prefix_sums.append(current_sum)

    # 4. Return SwarmConfig wrapping LazyList
    lazy_swarm = LazyList(prefix_sums, current_sum)
    return SwarmConfig(lazy_swarm)
