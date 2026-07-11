"""State Persistence and Context Compression module.

Contains utilities to serialize state dictionaries to YAML/JSON,
deserialize states, and compress dialogue history under token limits.
"""

import os
import json
import yaml
from typing import List, Dict, Any, Optional

class GraphNode:
    """Represents an entity node in the MemoryGraph."""
    def __init__(self, node_id: str, node_type: str, metadata: Optional[dict] = None):
        self.node_id = node_id
        self.node_type = node_type
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphNode":
        return cls(data["node_id"], data["node_type"], data.get("metadata", {}))


class GraphEdge:
    """Represents a relationship edge in the MemoryGraph."""
    def __init__(self, source_id: str, target_id: str, edge_type: str, metadata: Optional[dict] = None):
        self.source_id = source_id
        self.target_id = target_id
        self.edge_type = edge_type
        self.metadata = metadata or {}

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "target_id": self.target_id,
            "edge_type": self.edge_type,
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GraphEdge":
        return cls(data["source_id"], data["target_id"], data["edge_type"], data.get("metadata", {}))


class MemoryGraph:
    """A lightweight directed graph for GraphRAG entity storage."""
    def __init__(self):
        self.nodes: Dict[str, GraphNode] = {}
        # Adjacency list: node_id -> list of outgoing GraphEdges
        self.edges: Dict[str, List[GraphEdge]] = {}

    def add_node(self, node_id: str, node_type: str, metadata: Optional[dict] = None) -> None:
        if node_id not in self.nodes:
            self.nodes[node_id] = GraphNode(node_id, node_type, metadata)
            if node_id not in self.edges:
                self.edges[node_id] = []

    def add_edge(self, source_id: str, target_id: str, edge_type: str, metadata: Optional[dict] = None) -> None:
        if source_id not in self.nodes:
            self.add_node(source_id, "Unknown")
        if target_id not in self.nodes:
            self.add_node(target_id, "Unknown")
        
        edge = GraphEdge(source_id, target_id, edge_type, metadata)
        self.edges[source_id].append(edge)

    def traverse(self, start_node_id: str, max_depth: int = 2) -> Dict[str, Any]:
        """Traverses the graph BFS-style up to max_depth."""
        if start_node_id not in self.nodes:
            return {"nodes": [], "edges": []}

        visited_nodes = set()
        visited_edges = []
        
        queue = [(start_node_id, 0)]
        
        while queue:
            current_id, depth = queue.pop(0)
            
            if current_id not in visited_nodes:
                visited_nodes.add(current_id)
                
                if depth < max_depth:
                    for edge in self.edges.get(current_id, []):
                        visited_edges.append(edge)
                        if edge.target_id not in visited_nodes:
                            queue.append((edge.target_id, depth + 1))
                            
        return {
            "nodes": [self.nodes[n].to_dict() for n in visited_nodes],
            "edges": [e.to_dict() for e in visited_edges]
        }

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edges_list in self.edges.values() for edge in edges_list]
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryGraph":
        graph = cls()
        for n_data in data.get("nodes", []):
            graph.add_node(n_data["node_id"], n_data["node_type"], n_data.get("metadata", {}))
        for e_data in data.get("edges", []):
            graph.add_edge(e_data["source_id"], e_data["target_id"], e_data["edge_type"], e_data.get("metadata", {}))
        return graph


def compress_context(prompt_history: List[str], max_chars: int = 5000) -> List[str]:
    """Compresses the cumulative character history if it exceeds a maximum threshold."""
    cumulative_len = sum(len(x) for x in prompt_history)
    if cumulative_len <= max_chars:
        return prompt_history

    n = len(prompt_history)
    if n <= 1:
        return prompt_history

    mid = (n + 1) // 2
    oldest = prompt_history[:mid]
    remaining = prompt_history[mid:]

    oldest_joined = "\n".join(oldest)
    summary = f"[Semantic Summary: dense representation of {len(oldest_joined)} characters of history containing: {oldest_joined[:100]}...]"

    return [summary] + remaining


def hibernate_state(filepath: str, state_dict: dict) -> None:
    """Serializes the internal state dictionary to a local JSON/YAML file."""
    dir_name = os.path.dirname(filepath)
    if dir_name:
        os.makedirs(dir_name, exist_ok=True)
    if filepath.endswith((".yaml", ".yml")):
        with open(filepath, "w", encoding="utf-8") as f:
            yaml.safe_dump(state_dict, f)
    else:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(state_dict, f, indent=2)


def resume_state(filepath: str) -> dict:
    """Deserializes internal state from a local JSON/YAML file."""
    if not os.path.exists(filepath):
        raise FileNotFoundError(f"State file {filepath} not found.")
    if filepath.endswith((".yaml", ".yml")):
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    else:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f) or {}
