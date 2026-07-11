"""Swarm Topology Management module.

Maintains nodes and links topology (MESH, STAR, HIERARCHICAL) within a swarm
of agents, providing routing and node/role caches.
"""

from typing import List, Dict, Set, Optional


class SwarmTopology:
    """Topology management for a swarm of agents.

    Supports MESH, HIERARCHICAL, and STAR topologies.
    """

    def __init__(self, topology_type: str = "MESH", nodes: Optional[List[str]] = None) -> None:
        """Initializes the SwarmTopology.

        Args:
            topology_type: String format configuration (MESH, HIERARCHICAL, or STAR).
            nodes: Optional initial list of node string IDs.

        Raises:
            ValueError: If topology_type is not supported.
        """
        if topology_type not in ("MESH", "HIERARCHICAL", "STAR"):
            raise ValueError("Invalid topology type. Must be MESH, HIERARCHICAL, or STAR.")
        self.topology_type = topology_type
        self.nodes = nodes if nodes is not None else []
        self.edges: Dict[str, Set[str]] = {node: set() for node in self.nodes}
        self._role_cache: Dict[str, int] = {}
        self._build_topology()

    def _build_topology(self) -> None:
        """Constructs the inner edges mapping representation according to topology_type."""
        if not self.nodes:
            return
        # Clear existing edges
        self.edges = {node: set() for node in self.nodes}
        if self.topology_type == "MESH":
            for u in self.nodes:
                for v in self.nodes:
                    if u != v:
                        self.edges[u].add(v)
        elif self.topology_type == "STAR":
            hub = self.nodes[0]
            for spoke in self.nodes[1:]:
                self.edges[hub].add(spoke)
                self.edges[spoke].add(hub)
        elif self.topology_type == "HIERARCHICAL":
            # Build binary tree hierarchy
            n = len(self.nodes)
            for i in range(n):
                left = 2 * i + 1
                right = 2 * i + 2
                if left < n:
                    self.edges[self.nodes[i]].add(self.nodes[left])
                    self.edges[self.nodes[left]].add(self.nodes[i])
                if right < n:
                    self.edges[self.nodes[i]].add(self.nodes[right])
                    self.edges[self.nodes[right]].add(self.nodes[i])

    def add_node(self, node_id: str) -> None:
        """Adds a single node and rebuilds topology connections.

        Args:
            node_id: Node string ID.
        """
        if node_id not in self.edges:
            self.nodes.append(node_id)
            self.edges[node_id] = set()
            self._build_topology()

    def add_edge(self, u: str, v: str) -> None:
        """Registers a bidirectional link between two node string IDs.

        Args:
            u: First node ID.
            v: Second node ID.
        """
        self.add_node(u)
        self.add_node(v)
        self.edges[u].add(v)
        self.edges[v].add(u)

    def cache_roles(self, roles: List[str]) -> None:
        """Caches roles with their indexed order list.

        Args:
            roles: List of role strings.
        """
        self._role_cache = {role: idx for idx, role in enumerate(roles)}

    def get_cached_role_index(self, role: str) -> Optional[int]:
        """Looks up the cached index for a role string.

        Args:
            role: Target role string.

        Returns:
            The integer index, or None if not cached.
        """
        return self._role_cache.get(role)

    def find_route(self, start_id: str, end_id: str) -> List[str]:
        """BFS-based routing algorithm between nodes.

        Args:
            start_id: Beginning node ID.
            end_id: Destination node ID.

        Returns:
            List[str]: Node IDs path from start_id to end_id inclusive.
        """
        if start_id not in self.edges or end_id not in self.edges:
            return []
        if start_id == end_id:
            return [start_id]

        queue = [[start_id]]
        visited = {start_id}

        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == end_id:
                return path

            for neighbor in self.edges[node]:
                if neighbor not in visited:
                    visited.add(neighbor)
                    new_path = list(path)
                    new_path.append(neighbor)
                    queue.append(new_path)

        return []

