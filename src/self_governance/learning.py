"""Learning and Vector Storage module.

Enforces execution feedback loops, matrix scaling updates, binary heaps,
HNSW index vector memory, encryption/decryption mechanisms, AgentDB namespace store,
Smart Retrieval Pipeline, and MemoryBridge syncing.
"""

import os
import json
import logging
import threading
import math
import random
import struct
import time
import re
import hashlib
import heapq
from typing import Dict, Any, List, Set, Tuple, Optional
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .memory import MemoryGraph

logger = logging.getLogger("self_governance.learning")

LEARNING_STATE_FILE = ".learning_state.json"
_lock = threading.Lock()


def get_learning_state() -> Dict[str, Any]:
    """Retrieves the current learning logs and model state from storage.

    Returns:
        A dictionary containing runs_completed, success_rate, average_cycle_time,
        vulnerability_counts, and matrix_tuning scale factor.
    """
    if os.path.exists(LEARNING_STATE_FILE):
        try:
            with open(LEARNING_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load learning state: %s. Re-initializing.", e)

    return {
        "runs_completed": 0,
        "success_rate": 1.0,
        "average_cycle_time": 0.0,
        "vulnerability_counts": 0,
        "matrix_tuning": {"scale_factor": 1.0},
        "graph": {"nodes": [], "edges": []},
    }


def save_learning_state(state: Dict[str, Any]) -> None:
    """Saves the updated learning logs and model state to a JSON file.

    Writes atomically via a temp file + os.replace (peer-review batch,
    July 2026): opening LEARNING_STATE_FILE directly in "w" mode instantly
    truncates it to 0 bytes before the new content is written. A
    concurrent get_learning_state() call landing in that window sees a
    0-byte file, its json.load() raises, and it falls back to a fresh
    default dict -- if that caller then saves, it permanently overwrites
    all prior history. os.replace is atomic on POSIX and Windows: readers
    always see either the old complete file or the new complete file,
    never a partial/truncated one.

    Args:
        state: The learning state dictionary to serialize.
    """
    tmp_path = f"{LEARNING_STATE_FILE}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, LEARNING_STATE_FILE)
    except Exception as e:
        logger.error("Failed to save learning state: %s", e)


def track_learning_feedback(
    cycle_time: float, success: bool, security_breached: bool = False
) -> None:
    """Adjusts the dimensioning matrix scaling factors based on execution metrics.

    Applies a thread lock to safely increment runs_completed and adjust the rolling
    success_rate, average_cycle_time, and security-scaling factors.

    Args:
        cycle_time: Elapsed time of the development/succession cycle in seconds.
        success: True if the run completed successfully without errors.
        security_breached: True if security vulnerabilities were flagged.
    """
    with _lock:
        state = get_learning_state()

        # Calculate rolling averages
        n = state["runs_completed"]
        state["runs_completed"] = n + 1

        # Update success rate
        prev_success_sum = state["success_rate"] * n
        state["success_rate"] = (prev_success_sum + (1.0 if success else 0.0)) / (n + 1)

        # Update cycle time
        prev_cycle_sum = state["average_cycle_time"] * n
        state["average_cycle_time"] = (prev_cycle_sum + cycle_time) / (n + 1)

        if security_breached:
            state["vulnerability_counts"] += 1
            # Increase security agent staffing weights by tuning the scale factor
            state["matrix_tuning"]["scale_factor"] += 0.15
            logger.info(
                "Security risk logged. Scaling factor tuned up to %s",
                state["matrix_tuning"]["scale_factor"],
            )
            
            # Inject security event into graph
            graph = MemoryGraph.from_dict(state.get("graph", {"nodes": [], "edges": []}))
            event_id = f"evt_sec_{int(time.time())}"
            graph.add_node(event_id, "Event", {"type": "SecurityBreach", "timestamp": time.time()})
            # Link to the latest session if available
            distillation_log = state.get("distillation_log", [])
            if distillation_log:
                latest_session = distillation_log[-1]
                session_id = f"session_{int(latest_session['timestamp'])}"
                graph.add_edge(event_id, session_id, "OCCURRED_DURING")
            state["graph"] = graph.to_dict()

        save_learning_state(state)


def distill_friction(adapter: Any, roster: list, cycles: int, temperature: float) -> str:
    """Invokes a Distiller persona to synthesize a handover narrative."""
    from self_governance.agency_agents_adapter import DynamicAgentFactory
    factory = DynamicAgentFactory()
    distiller = factory.synthesize_sdlc_agent("Engineering Operations Distiller", adapter)
    
    prompt = (
        f"You are the {distiller['role']}. A swarm succession session just concluded.\n"
        f"Roster: {', '.join(roster)}\n"
        f"TETD Cycles Needed: {cycles}\n"
        f"Final Temperature: {temperature:.2f}\n\n"
        "Analyze this data. High cycles and temperature indicate significant strategic friction "
        "and thermal escapes during consensus. Provide a 2-3 sentence narrative Handover Document "
        "explaining why this friction likely occurred and what the final roster resolves."
    )
    return adapter._run_or_fallback(prompt, fallback_msg="Friction due to strategy misalignment.").get("output", "")

def distill_session(session_result: Any, roster: list, cycles: int, temperature: float, adapter: Optional[Any] = None) -> None:
    """Distills a completed succession session into the persistent learning state.

    Extracts patterns from the session result and stores them so that future
    succession sessions can avoid repeating mistakes and build on what worked.

    Args:
        session_result: The ConsensusResult from the session (unused fields tolerated).
        roster: The approved roster list from this session.
        cycles: Number of TETD consensus iterations consumed.
        temperature: Final temperature when consensus was reached.
        adapter: Optional adapter to run the Distiller persona for narrative generation.
    """
    with _lock:
        state = get_learning_state()

        # Coerce temperature to a plain float to guard against mock objects in tests
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = 0.0

        # Generate narrative handover if adapter provided and friction was high
        handover_narrative = ""
        if adapter and cycles > 1:
            try:
                handover_narrative = distill_friction(adapter, roster, cycles, temperature)
            except Exception as e:
                logger.warning("Distiller persona failed to generate narrative: %s", e)

        # Build the distilled entry
        entry = {
            "timestamp": time.time(),
            "roster": roster,
            "cycles_needed": cycles,
            "final_temperature": temperature,
            "pattern": (
                f"Roster [{', '.join(roster)}] reached consensus in {cycles} cycle(s) "
                f"at temperature {temperature:.2f}."
            ),
            "anti_pattern": (
                "High cycle count indicates initial roster misalignment."
                if cycles > 3 else ""
            ),
            "handover_narrative": handover_narrative,
        }

        # Store in rolling distillation log (cap at 50 entries)
        distillation_log = state.get("distillation_log", [])
        distillation_log.append(entry)
        if len(distillation_log) > 50:
            distillation_log = distillation_log[-50:]
        state["distillation_log"] = distillation_log

        # Update GraphRAG memory
        graph = MemoryGraph.from_dict(state.get("graph", {"nodes": [], "edges": []}))
        session_id = f"session_{int(float(str(entry.get('timestamp', 0))))}"
        graph.add_node(session_id, "Session", {"cycles": cycles, "temperature": temperature})
        
        for role in roster:
            role_id = f"persona_{role.replace(' ', '_').lower()}"
            graph.add_node(role_id, "Persona", {"name": role})
            graph.add_edge(role_id, session_id, "PARTICIPATED_IN")
            
        state["graph"] = graph.to_dict()

        # Update summary metrics
        prev_count = state.get("sessions_distilled", 0)
        prev_avg_cycles = state.get("avg_cycles_needed", 0.0)
        state["sessions_distilled"] = prev_count + 1
        state["avg_cycles_needed"] = (
            (prev_avg_cycles * prev_count + cycles) / (prev_count + 1)
        )
        state["last_approved_roster"] = roster
        state["last_session_temperature"] = temperature

        save_learning_state(state)
        logger.info(
            "Session distilled: %d total sessions, avg cycles %.2f.",
            state["sessions_distilled"],
            state["avg_cycles_needed"],
        )


def restore_session_context() -> dict:
    """Restores learning context from the persistent state for the current session.

    Called at nudger startup to prime the orchestrator with institutional memory
    from previous succession sessions.

    Returns:
        A dict with keys: last_approved_roster, avg_cycles_needed,
        sessions_distilled, last_session_temperature, recent_patterns (list of str).
    """
    state = get_learning_state()
    distillation_log = state.get("distillation_log", [])
    recent_patterns = [
        entry.get("pattern", "")
        for entry in distillation_log[-5:]  # last 5 session patterns
        if entry.get("pattern")
    ]
    # Retrieve graph context using a brief traversal from the last session
    graph_summary = {}
    if distillation_log:
        latest_session = distillation_log[-1]
        session_id = f"session_{int(latest_session['timestamp'])}"
        graph = MemoryGraph.from_dict(state.get("graph", {"nodes": [], "edges": []}))
        traversal = graph.traverse(session_id, max_depth=1)
        graph_summary = {
            "node_count": len(traversal["nodes"]),
            "edge_count": len(traversal["edges"]),
            "roles_involved": [n["node_id"] for n in traversal["nodes"] if n["node_type"] == "Persona"]
        }

    context = {
        "last_approved_roster": state.get("last_approved_roster", []),
        "avg_cycles_needed": state.get("avg_cycles_needed", 0.0),
        "sessions_distilled": state.get("sessions_distilled", 0),
        "last_session_temperature": state.get("last_session_temperature", 1.0),
        "recent_patterns": recent_patterns,
        "graph_context": graph_summary,
    }
    if context["sessions_distilled"] > 0:
        logger.info(
            "Restored learning context: %d prior sessions, avg %.2f cycles, "
            "last roster: %s",
            context["sessions_distilled"],
            context["avg_cycles_needed"],
            context["last_approved_roster"],
        )
    return context


# ----------------------------------------------------
# Persistent Vector Memory Components
# ----------------------------------------------------

class BinaryMinHeap:
    """A standard binary min-heap implementation wrapper."""

    def __init__(self):
        """Initializes the BinaryMinHeap."""
        self.data = []

    def push(self, val: Any) -> None:
        """Pushes an element onto the min-heap.

        Args:
            val: The value (e.g. tuple or float) to store.
        """
        heapq.heappush(self.data, val)

    def pop(self) -> Any:
        """Pops the smallest element from the min-heap.

        Returns:
            The popped element.
        """
        return heapq.heappop(self.data)

    def peek(self) -> Optional[Any]:
        """Peeks at the smallest element in the min-heap without removing it.

        Returns:
            The smallest element, or None if heap is empty.
        """
        return self.data[0] if self.data else None

    def __len__(self) -> int:
        """Returns the size of the min-heap.

        Returns:
            Size of the heap array.
        """
        return len(self.data)


class BinaryMaxHeap:
    """A binary max-heap implementation wrapper using negated scores."""

    def __init__(self):
        """Initializes the BinaryMaxHeap."""
        self.data = []

    def push(self, val: Tuple[float, Any]) -> None:
        """Pushes a tuple onto the max-heap, negating the score.

        Args:
            val: A tuple of (score (float), value (Any)).
        """
        score, item = val
        heapq.heappush(self.data, (-score, item))

    def pop(self) -> Tuple[float, Any]:
        """Pops the largest element from the max-heap.

        Returns:
            A tuple of (score (float), value (Any)).
        """
        neg_score, item = heapq.heappop(self.data)
        return -neg_score, item

    def peek(self) -> Optional[Tuple[float, Any]]:
        """Peeks at the largest element in the max-heap.

        Returns:
            A tuple of (score (float), value (Any)), or None if empty.
        """
        if not self.data:
            return None
        neg_score, item = self.data[0]
        return -neg_score, item

    def __len__(self) -> int:
        """Returns the size of the max-heap.

        Returns:
            Size of the heap array.
        """
        return len(self.data)


class HNSWIndex:
    """A pure-Python Hierarchical Navigable Small World (HNSW) vector index.

    Optimizes similarity search over normalized multi-dimensional float vectors.
    """

    def __init__(self, M: int = 16, M0: int = 32, efConstruction: int = 64, efSearch: int = 50):
        """Initializes the HNSWIndex.

        Args:
            M: Max number of connections per node in layers > 0.
            M0: Max number of connections per node in layer 0.
            efConstruction: Number of dynamic candidates to check during construction.
            efSearch: Number of dynamic candidates to check during query search.
        """
        self.M = M
        self.M0 = M0
        self.efConstruction = efConstruction
        self.efSearch = efSearch
        self.mL = 1.0 / math.log(M) if M > 1 else 1.0
        self.enter_node: Optional[int] = None
        self.max_level: int = -1
        self.nodes: Dict[int, Dict[str, Any]] = {}  # node_id -> {"vector": list[float], "level": int, "connections": dict[int, list[int]]}
        self.max_level_limit = 16
        # Tombstones (not physical removal): rewiring neighbor lists across
        # every layer to physically excise a node is nontrivial and easy to
        # get wrong; tombstoning is the standard HNSW approach for deletion
        # -- the node stays in the graph for traversal/connectivity but is
        # filtered out of search() results. Not yet included in
        # serialize()/deserialize()'s binary format (a format-version bump
        # would be needed), so a deleted node currently reappears in results
        # after a save/reload round-trip -- a known limitation, not silently
        # claimed as fully solved.
        self.deleted: Set[int] = set()

    def delete(self, node_id: int) -> None:
        """Tombstones a node so it's excluded from future search() results.

        Args:
            node_id: The node identifier to remove from result visibility.
        """
        self.deleted.add(node_id)

    @staticmethod
    def normalize(v: List[float]) -> List[float]:
        """Normalizes a vector to unit length (L2 norm).

        Args:
            v: Input list of floats.

        Returns:
            The normalized vector list.
        """
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            return v
        return [x / norm for x in v]

    @staticmethod
    def dot_product(v1: List[float], v2: List[float]) -> float:
        """Computes the dot product of two vectors.

        Args:
            v1: First vector.
            v2: Second vector.

        Returns:
            The dot product float value.
        """
        return sum(x * y for x, y in zip(v1, v2))

    def search_layer(self, q_vec: List[float], ep: Set[int], ef: int, layer: int) -> List[Tuple[float, int]]:
        """Searches for nearest neighbors within a specific HNSW layer.

        Args:
            q_vec: Normalized query vector.
            ep: Entrance point node IDs.
            ef: Number of candidates to evaluate.
            layer: Target index layer.

        Returns:
            A list of tuples (similarity (float), node_id (int)) sorted descending.
        """
        visited = set(ep)
        C: List[Tuple[float, int]] = []
        W: List[Tuple[float, int]] = []

        for e in ep:
            sim = self.dot_product(q_vec, self.nodes[e]["vector"])
            heapq.heappush(C, (-sim, e))
            heapq.heappush(W, (sim, e))

        while C:
            curr_neg_sim, c = heapq.heappop(C)
            curr_sim = -curr_neg_sim

            if curr_sim < W[0][0]:
                break

            neighbors = self.nodes[c]["connections"].get(layer, [])
            for neighbor in neighbors:
                if neighbor not in visited:
                    visited.add(neighbor)

                    neighbor_sim = self.dot_product(q_vec, self.nodes[neighbor]["vector"])
                    worst_sim = W[0][0]

                    if neighbor_sim > worst_sim or len(W) < ef:
                        heapq.heappush(C, (-neighbor_sim, neighbor))
                        heapq.heappush(W, (neighbor_sim, neighbor))

                        if len(W) > ef:
                            heapq.heappop(W)

        return sorted(W, key=lambda x: x[0], reverse=True)

    def insert(self, node_id: int, vector: List[float], level: Optional[int] = None) -> None:
        """Inserts a new vector node into the HNSW index.

        Args:
            node_id: Unique identifier for the node.
            vector: Float vector list to index.
            level: Optional level to assign; if None, random level is computed.
        """
        # A re-inserted node_id must come off the tombstone set, or it stays
        # invisible to search() forever (peer-review batch, July 2026):
        # AgentDB.delete() pops a key's node_id from its own bookkeeping, so
        # a later insert() for the *same key* deterministically regenerates
        # the identical node_id (dual_hash is a pure function of the key,
        # and the collision-avoidance loop no longer sees this id as taken)
        # -- but this method never removed it from self.deleted, so the
        # freshly-reinserted vector was silently filtered out of every
        # subsequent search() result.
        self.deleted.discard(node_id)
        vector = self.normalize(vector)

        if not self.nodes:
            self.nodes[node_id] = {
                "vector": vector,
                "level": level if level is not None else 0,
                "connections": {}
            }
            self.enter_node = node_id
            self.max_level = level if level is not None else 0
            return

        if level is None:
            # HNSW level sampling: statistical layer assignment, not security.
            r = random.random()  # nosec B311
            level = 0
            while r < 0.5 and level < self.max_level_limit:
                level += 1
                r = random.random()  # nosec B311

        if self.enter_node is None:
            # assert would vanish under `python -O`; this invariant must hold
            # in production too (an index with nodes always has an entry point).
            raise RuntimeError("HNSW index has nodes but no entry node")
        curr_ep: Set[int] = {self.enter_node}
        for lvl in range(self.max_level, level, -1):
            candidates = self.search_layer(vector, curr_ep, ef=1, layer=lvl)
            if candidates:
                curr_ep = {candidates[0][1]}

        self.nodes[node_id] = {
            "vector": vector,
            "level": level,
            "connections": {}
        }

        for lvl in range(min(level, self.max_level), -1, -1):
            candidates = self.search_layer(vector, curr_ep, ef=self.efConstruction, layer=lvl)
            m_limit = self.M0 if lvl == 0 else self.M

            neighbors = [nb_id for sim, nb_id in candidates[:m_limit]]

            self.nodes[node_id]["connections"][lvl] = neighbors
            for nb in neighbors:
                if lvl not in self.nodes[nb]["connections"]:
                    self.nodes[nb]["connections"][lvl] = []
                self.nodes[nb]["connections"][lvl].append(node_id)

                nb_limit = self.M0 if lvl == 0 else self.M
                if len(self.nodes[nb]["connections"][lvl]) > nb_limit:
                    nb_vec = self.nodes[nb]["vector"]
                    nb_conn = self.nodes[nb]["connections"][lvl]
                    nb_conn_sims = [(self.dot_product(nb_vec, self.nodes[c]["vector"]), c) for c in nb_conn]
                    nb_conn_sims.sort(key=lambda x: x[0], reverse=True)
                    self.nodes[nb]["connections"][lvl] = [c for sim, c in nb_conn_sims[:nb_limit]]

            curr_ep = {c[1] for c in candidates}

        if level > self.max_level:
            self.max_level = level
            self.enter_node = node_id

    def search(self, q_vec: List[float], k: int = 5) -> List[Tuple[float, int]]:
        """Queries the HNSW index to retrieve the k-nearest neighbors.

        Args:
            q_vec: Float query vector list.
            k: Limit of neighbors to retrieve.

        Returns:
            A list of tuples (similarity (float), node_id (int)).
        """
        if not self.nodes:
            return []
        q_vec = self.normalize(q_vec)
        if self.enter_node is None:
            raise RuntimeError("HNSW index has nodes but no entry node")
        curr_ep: Set[int] = {self.enter_node}
        for lvl in range(self.max_level, 0, -1):
            candidates = self.search_layer(q_vec, curr_ep, ef=1, layer=lvl)
            if candidates:
                curr_ep = {candidates[0][1]}
        # Over-fetch past k so filtering out tombstoned nodes below still
        # has a fair chance of returning k live results (deleted nodes are
        # not stripped from search_layer's own graph traversal, only from
        # what's ultimately returned).
        overfetch = min(len(self.deleted), 50)  # bounded so mass deletions can't blow up search cost
        results = self.search_layer(q_vec, curr_ep, ef=max(self.efSearch, k) + overfetch, layer=0)
        live_results = [r for r in results if r[1] not in self.deleted]
        return live_results[:k]

    def serialize(self) -> bytes:
        """Serializes the index into a binary byte representation.

        Returns:
            Byte string of index structure.
        """
        data = bytearray()
        data.extend(b"HNSW\x01")
        ep = self.enter_node if self.enter_node is not None else -1
        data.extend(struct.pack(">IIIIqii", self.M, self.M0, self.efConstruction, self.efSearch, ep, self.max_level, len(self.nodes)))

        for node_id, node_data in self.nodes.items():
            vec = node_data["vector"]
            lvl = node_data["level"]
            connections = node_data["connections"]

            data.extend(struct.pack(">qii", node_id, lvl, len(vec)))
            data.extend(struct.pack(f">{len(vec)}d", *vec))

            data.extend(struct.pack(">i", len(connections)))
            for layer_idx, conns in connections.items():
                data.extend(struct.pack(">ii", layer_idx, len(conns)))
                data.extend(struct.pack(f">{len(conns)}q", *conns))

        return bytes(data)

    @classmethod
    def deserialize(cls, data: bytes) -> "HNSWIndex":
        """Reconstructs an HNSWIndex from a serialized binary byte representation.

        Args:
            data: Binary byte string representing index.

        Returns:
            HNSWIndex instance.
        """
        if not data.startswith(b"HNSW\x01"):
            raise ValueError("Invalid HNSW header")

        offset = 5
        M, M0, efConstruction, efSearch, ep, max_level, num_nodes = struct.unpack_from(">IIIIqii", data, offset)
        offset += 32

        index = cls(M=M, M0=M0, efConstruction=efConstruction, efSearch=efSearch)
        index.enter_node = ep if ep != -1 else None
        index.max_level = max_level

        for _ in range(num_nodes):
            node_id, lvl, vec_len = struct.unpack_from(">qii", data, offset)
            offset += 16

            vec_fmt = f">{vec_len}d"
            vec_size = vec_len * 8
            vector = list(struct.unpack_from(vec_fmt, data, offset))
            offset += vec_size

            connections = {}
            num_layers, = struct.unpack_from(">i", data, offset)
            offset += 4

            for _ in range(num_layers):
                layer_idx, num_conns = struct.unpack_from(">ii", data, offset)
                offset += 8

                conns_fmt = f">{num_conns}q"
                conns_size = num_conns * 8
                conns = list(struct.unpack_from(conns_fmt, data, offset))
                offset += conns_size

                connections[layer_idx] = conns

            index.nodes[node_id] = {
                "vector": vector,
                "level": lvl,
                "connections": connections
            }

        return index


# ----------------------------------------------------
# Key Hashing Functions
# ----------------------------------------------------

def djb2_hash(key: str) -> int:
    """Computes a djb2 hash value for a key string.

    Args:
        key: Input string.

    Returns:
        An integer hash value bounded to 53 bits.
    """
    h = 5381
    for char in key:
        h = ((h << 5) + h) + ord(char)
    return h & 0x1FFFFFFFFFFFFF


def sdbm_hash(key: str) -> int:
    """Computes an sdbm hash value for a key string.

    Args:
        key: Input string.

    Returns:
        An integer hash value bounded to 53 bits.
    """
    h = 0
    for char in key:
        h = ord(char) + (h << 6) + (h << 16) - h
    return h & 0x1FFFFFFFFFFFFF


def dual_hash(key: str) -> int:
    """Computes a dual hash by XORing djb2 and sdbm hashes.

    Args:
        key: Input string.

    Returns:
        A combined integer hash value bounded to 53 bits.
    """
    return (djb2_hash(key) ^ sdbm_hash(key)) & 0x1FFFFFFFFFFFFF


# ----------------------------------------------------
# Encryption Helper Functions
# ----------------------------------------------------

def get_encryption_key() -> bytes:
    """Parses and sanitizes the CLAUDE_FLOW_ENCRYPTION_KEY environment key.

    Returns:
        A 32-byte secret key.

    Raises:
        ValueError: If the key environment variable is not defined.
    """
    key_str = os.environ.get("CLAUDE_FLOW_ENCRYPTION_KEY")
    if not key_str:
        raise ValueError("CLAUDE_FLOW_ENCRYPTION_KEY is not set.")

    # Hex decoding (64 hex characters -> 32 bytes)
    if len(key_str) == 64:
        try:
            return bytes.fromhex(key_str)
        except ValueError:
            pass

    # Base64 decoding
    import base64
    try:
        decoded = base64.b64decode(key_str)
        if len(decoded) in (16, 24, 32):
            return decoded
    except Exception:  # nosec B110
        # Not base64 — fall through to the padding/trimming path below.
        pass

    # Fallback padding/trimming to exactly 32 bytes
    key_bytes = key_str.encode("utf-8")
    if len(key_bytes) >= 32:
        return key_bytes[:32]
    return key_bytes.ljust(32, b"\x00")


def encrypt_data(plaintext: bytes, require_key: bool = False) -> bytes:
    """Encrypts plaintext bytes using AES-GCM-256.

    Args:
        plaintext: Bytes data to encrypt.
        require_key: If True, raises an error if environment key is missing.

    Returns:
        Encrypted byte string prefixed with b"RFE1", or plaintext fallback.

    Raises:
        ValueError: If require_key is True and key is missing.
    """
    key_str = os.environ.get("CLAUDE_FLOW_ENCRYPTION_KEY")
    if not key_str:
        if require_key:
            raise ValueError("CLAUDE_FLOW_ENCRYPTION_KEY is required for encryption.")
        logger.warning("CLAUDE_FLOW_ENCRYPTION_KEY is not set. Falling back to PLAINTEXT.")
        return plaintext

    key = get_encryption_key()
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ciphertext = aesgcm.encrypt(iv, plaintext, None)
    return b"RFE1" + iv + ciphertext


def decrypt_data(ciphertext: bytes) -> bytes:
    """Decrypts ciphertext bytes using AES-GCM-256 if encrypted (RFE1 prefix).

    Args:
        ciphertext: Encrypted or plain data bytes.

    Returns:
        Decrypted byte string.

    Raises:
        ValueError: If decryption fails or required key is missing.
    """
    if not ciphertext.startswith(b"RFE1"):
        return ciphertext

    key_str = os.environ.get("CLAUDE_FLOW_ENCRYPTION_KEY")
    if not key_str:
        raise ValueError("Encrypted data detected (RFE1) but CLAUDE_FLOW_ENCRYPTION_KEY is not set.")

    key = get_encryption_key()
    aesgcm = AESGCM(key)
    iv = ciphertext[4:16]
    payload = ciphertext[16:]
    try:
        return aesgcm.decrypt(iv, payload, None)
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")


# ----------------------------------------------------
# Memory Record & AgentDB Namespace Store
# ----------------------------------------------------

class MemoryRecord:
    """Represents a database node record storing text key and associated vector."""

    def __init__(self, key: str, vector: List[float], namespace: str, timestamp: Optional[float] = None, metadata: Optional[Dict[Any, Any]] = None):
        """Initializes the MemoryRecord.

        Args:
            key: Text key identifier.
            vector: Multi-dimensional embedding float list.
            namespace: Storage namespace name (e.g. 'patterns').
            timestamp: Optional creation timestamp epoch float.
            metadata: Optional dictionary of attributes.
        """
        self.key = key
        self.vector = vector
        self.namespace = namespace
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.metadata = metadata if metadata is not None else {}


class AgentDB:
    """Namespace database indexing multiple HNSWIndex collections."""

    def __init__(self, M: int = 16, M0: int = 32, efConstruction: int = 64, efSearch: int = 50):
        """Initializes the AgentDB.

        Args:
            M: HNSW connections parameter.
            M0: HNSW layer 0 connections parameter.
            efConstruction: HNSW construction dynamic check limit.
            efSearch: HNSW search candidate limit.
        """
        self.namespaces = {
            "patterns": HNSWIndex(M, M0, efConstruction, efSearch),
            "succession": HNSWIndex(M, M0, efConstruction, efSearch),
            "feedback": HNSWIndex(M, M0, efConstruction, efSearch),
            "telemetry": HNSWIndex(M, M0, efConstruction, efSearch),
        }
        self.key_to_id: Dict[str, Dict[str, int]] = {ns: {} for ns in self.namespaces}
        self.id_to_key: Dict[str, Dict[int, str]] = {ns: {} for ns in self.namespaces}
        self.records: Dict[str, Dict[int, MemoryRecord]] = {ns: {} for ns in self.namespaces}

    def insert(self, namespace: str, key: str, vector: List[float], timestamp: Optional[float] = None, metadata: Optional[Dict[Any, Any]] = None) -> int:
        """Inserts a memory entry into a namespace HNSW index.

        Args:
            namespace: Target namespace key.
            key: Identifiable text key.
            vector: Floats vector list.
            timestamp: Optional timestamp float.
            metadata: Optional metadata dictionary.

        Returns:
            The generated integer node ID.

        Raises:
            ValueError: If the namespace is not supported.
        """
        if namespace not in self.namespaces:
            raise ValueError(f"Invalid namespace: {namespace}")

        index = self.namespaces[namespace]
        key_to_id = self.key_to_id[namespace]
        id_to_key = self.id_to_key[namespace]

        if key in key_to_id:
            node_id = key_to_id[key]
            index.insert(node_id, vector)
            self.records[namespace][node_id] = MemoryRecord(key, vector, namespace, timestamp, metadata)
            return node_id

        h = dual_hash(key)
        while h in id_to_key:
            h = (h + 1) & 0x1FFFFFFFFFFFFF

        node_id = h
        key_to_id[key] = node_id
        id_to_key[node_id] = key

        index.insert(node_id, vector)
        self.records[namespace][node_id] = MemoryRecord(key, vector, namespace, timestamp, metadata)
        return node_id

    def delete(self, namespace: str, key: str) -> bool:
        """Removes a memory entry: tombstones its vector in the namespace's
        HNSWIndex (see HNSWIndex.delete) and drops its bookkeeping entries,
        so a decommissioned/fully-decayed memory stops showing up in search
        results and stops holding a slot in key_to_id/id_to_key/records.

        Args:
            namespace: Target namespace key.
            key: The identifiable text key to remove.

        Returns:
            True if a matching entry was found and removed, False if the
            key wasn't present in this namespace.

        Raises:
            ValueError: If the namespace is not supported.
        """
        if namespace not in self.namespaces:
            raise ValueError(f"Invalid namespace: {namespace}")

        key_to_id = self.key_to_id[namespace]
        if key not in key_to_id:
            return False

        node_id = key_to_id.pop(key)
        self.id_to_key[namespace].pop(node_id, None)
        self.records[namespace].pop(node_id, None)
        self.namespaces[namespace].delete(node_id)
        return True

    def batch_insert(self, namespace: str, items: List[dict]) -> List[int]:
        """Inserts a batch list of dictionaries into a namespace.

        Args:
            namespace: Target namespace key.
            items: List of dictionaries with key, vector, and optional metadata/timestamp.

        Returns:
            List of generated node IDs.
        """
        chunk_size = 50
        inserted_ids = []
        for i in range(0, len(items), chunk_size):
            chunk = items[i:i + chunk_size]
            for item in chunk:
                node_id = self.insert(
                    namespace=namespace,
                    key=item["key"],
                    vector=item["vector"],
                    timestamp=item.get("timestamp"),
                    metadata=item.get("metadata")
                )
                inserted_ids.append(node_id)
        return inserted_ids

    def serialize(self) -> bytes:
        """Serializes all namespaces and records into binary byte format.

        Returns:
            Byte string payload.
        """
        data = bytearray()
        data.extend(b"AGDB\x01")
        data.extend(struct.pack(">i", len(self.namespaces)))

        for ns_name, index in self.namespaces.items():
            ns_bytes = ns_name.encode("utf-8")
            data.extend(struct.pack(">i", len(ns_bytes)))
            data.extend(ns_bytes)

            idx_bytes = index.serialize()
            data.extend(struct.pack(">i", len(idx_bytes)))
            data.extend(idx_bytes)

            rec_dict = {}
            for node_id, rec in self.records[ns_name].items():
                rec_dict[str(node_id)] = {
                    "key": rec.key,
                    "timestamp": rec.timestamp,
                    "metadata": rec.metadata
                }

            meta_dict = {
                "key_to_id": self.key_to_id[ns_name],
                "records": rec_dict
            }
            meta_bytes = json.dumps(meta_dict).encode("utf-8")
            data.extend(struct.pack(">i", len(meta_bytes)))
            data.extend(meta_bytes)

        return bytes(data)

    @classmethod
    def deserialize(cls, data: bytes) -> "AgentDB":
        """Deserializes a byte string representation into a new AgentDB instance.

        Args:
            data: Binary byte payload.

        Returns:
            AgentDB instance.
        """
        if not data.startswith(b"AGDB\x01"):
            raise ValueError("Invalid AgentDB header")

        offset = 5
        num_ns, = struct.unpack_from(">i", data, offset)
        offset += 4

        db = cls()
        db.namespaces.clear()
        db.key_to_id.clear()
        db.id_to_key.clear()
        db.records.clear()

        for _ in range(num_ns):
            ns_len, = struct.unpack_from(">i", data, offset)
            offset += 4
            ns_name = data[offset:offset+ns_len].decode("utf-8")
            offset += ns_len

            idx_len, = struct.unpack_from(">i", data, offset)
            offset += 4
            idx_bytes = data[offset:offset+idx_len]
            offset += idx_len

            index = HNSWIndex.deserialize(idx_bytes)

            meta_len, = struct.unpack_from(">i", data, offset)
            offset += 4
            meta_bytes = data[offset:offset+meta_len]
            offset += meta_len

            meta_dict = json.loads(meta_bytes.decode("utf-8"))

            db.namespaces[ns_name] = index
            db.key_to_id[ns_name] = meta_dict["key_to_id"]
            db.id_to_key[ns_name] = {int(v): k for k, v in meta_dict["key_to_id"].items()}

            db.records[ns_name] = {}
            for node_id_str, r_data in meta_dict["records"].items():
                node_id = int(node_id_str)
                db.records[ns_name][node_id] = MemoryRecord(
                    key=r_data["key"],
                    vector=index.nodes[node_id]["vector"],
                    namespace=ns_name,
                    timestamp=r_data["timestamp"],
                    metadata=r_data["metadata"]
                )

        return db

    def save_to_file(self, file_path: str, require_key: bool = False) -> None:
        """Saves database state to disk, optionally encrypting.

        Args:
            file_path: Output target file path.
            require_key: If True, fails if encryption key is missing.
        """
        plaintext = self.serialize()
        ciphertext = encrypt_data(plaintext, require_key=require_key)

        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, mode=0o700, exist_ok=True)

        tmp_path = file_path + ".tmp"
        with open(tmp_path, "wb") as f:
            f.write(ciphertext)

        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, file_path)

    @classmethod
    def load_from_file(cls, file_path: str) -> "AgentDB":
        """Loads database state from a file, decrypting if necessary.

        Args:
            file_path: Input database file path.

        Returns:
            The loaded AgentDB instance.

        Raises:
            FileNotFoundError: If file_path does not exist.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Database file not found: {file_path}")

        with open(file_path, "rb") as f:
            ciphertext = f.read()

        plaintext = decrypt_data(ciphertext)
        return cls.deserialize(plaintext)


# ----------------------------------------------------
# Smart Retrieval Pipeline
# ----------------------------------------------------

class SmartRetrievalPipeline:
    """Orchestrates query vector retrieval across namespaces using advanced filters."""

    def __init__(self, db: AgentDB):
        """Initializes the SmartRetrievalPipeline.

        Args:
            db: Target AgentDB instance.
        """
        self.db = db

    def retrieve(self,
                 query_vector: List[float],
                 namespaces: Optional[List[str]] = None,
                 limit: int = 5,
                 k_rrf: int = 60,
                 half_life_ms: float = 3600000.0,
                 mmr_lambda: float = 0.5,
                 current_time: Optional[float] = None) -> List[MemoryRecord]:
        """Retrieves and ranks records using RRF, recency decay, MMR, and round-robin.

        Args:
            query_vector: Embedding vector of query.
            namespaces: Namespaces list to search. Defaults to all.
            limit: Return result size count.
            k_rrf: RRF constant parameter.
            half_life_ms: Recency decay half-life in milliseconds.
            mmr_lambda: Tradeoff factor between similarity and diversity.
            current_time: Optional mock current time.

        Returns:
            List of sorted MemoryRecord objects.
        """
        if current_time is None:
            current_time = time.time()

        if namespaces is None:
            namespaces = list(self.db.namespaces.keys())

        # Phase 1: Query Expansion
        q_vec = HNSWIndex.normalize(query_vector)
        q_vars = [q_vec]
        dim = len(q_vec)
        if dim > 0:
            q2 = [x + 0.02 * math.sin(i) for i, x in enumerate(q_vec)]
            q_vars.append(HNSWIndex.normalize(q2))
            q3 = [x - 0.02 * math.cos(i) for i, x in enumerate(q_vec)]
            q_vars.append(HNSWIndex.normalize(q3))
        else:
            q_vars.extend([q_vec, q_vec])

        # Phase 2: Reciprocal Rank Fusion (RRF)
        rrf_scores: Dict[Tuple[str, int], float] = {}
        for ns in namespaces:
            index = self.db.namespaces[ns]
            for q_var in q_vars:
                results = index.search(q_var, k=limit * 4)
                for rank, (sim, node_id) in enumerate(results):
                    r = rank + 1
                    key = (ns, node_id)
                    rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (k_rrf + r)

        # Phase 3: Recency Boost / Decay
        boosted_scores = {}
        for (ns, node_id), score in rrf_scores.items():
            record = self.db.records[ns][node_id]
            age_ms = max(0.0, (current_time - record.timestamp) * 1000.0)
            boost = 0.5 ** (age_ms / half_life_ms)
            boosted_scores[(ns, node_id)] = score * boost

        # Phase 4: MMR Jaccard Diversity
        candidates = sorted(boosted_scores.items(), key=lambda x: x[1], reverse=True)
        selected: List[Tuple[str, int, MemoryRecord]] = []
        remaining = [(ns, node_id, score) for (ns, node_id), score in candidates]

        def get_tokens(text: str) -> Set[str]:
            return set(re.findall(r"\w+", text.lower())) if text else set()

        while remaining and len(selected) < limit * 2:
            best_mmr = -999999.0
            best_idx = -1

            for idx, (ns, node_id, score) in enumerate(remaining):
                record = self.db.records[ns][node_id]
                rec_text = f"{record.key} {ns} " + " ".join(str(v) for v in record.metadata.values())
                rec_tokens = get_tokens(rec_text)

                max_jaccard = 0.0
                for sel_ns, sel_node_id, sel_rec in selected:
                    sel_text = f"{sel_rec.key} {sel_ns} " + " ".join(str(v) for v in sel_rec.metadata.values())
                    sel_tokens = get_tokens(sel_text)

                    union_len = len(rec_tokens | sel_tokens)
                    jacc = len(rec_tokens & sel_tokens) / union_len if union_len > 0 else 0.0

                    if jacc > max_jaccard:
                        max_jaccard = jacc

                mmr_score = mmr_lambda * score - (1.0 - mmr_lambda) * max_jaccard
                if mmr_score > best_mmr:
                    best_mmr = mmr_score
                    best_idx = idx

            if best_idx != -1:
                ns, node_id, score = remaining.pop(best_idx)
                selected.append((ns, node_id, self.db.records[ns][node_id]))
            else:
                break

        # Phase 5: Interleaved Round-Robin
        groups: Dict[str, List[MemoryRecord]] = {}
        for ns, node_id, record in selected:
            sess_id = record.metadata.get("session_id", "default_session")
            groups.setdefault(sess_id, []).append(record)

        interleaved = []
        group_lists = list(groups.values())
        max_group_len = max(len(g) for g in group_lists) if group_lists else 0

        for step in range(max_group_len):
            for g in group_lists:
                if step < len(g):
                    interleaved.append(g[step])
                    if len(interleaved) >= limit:
                        break
            if len(interleaved) >= limit:
                break

        return interleaved


# ----------------------------------------------------
# Memory Bridge & Text Embedding Helper
# ----------------------------------------------------

def embed_text(text: str, dimension: int = 8) -> List[float]:
    """Generates a mock vector representation of text by char ord summation.

    Args:
        text: Input text string.
        dimension: Length of output vector list.

    Returns:
        Normalized float vector list.
    """
    vec = [0.0] * dimension
    for i, char in enumerate(text):
        vec[i % dimension] += ord(char)
    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]
    return vec


class MemoryBridge:
    """Manages file ingestion context, write-ahead logs, and file pruning."""

    def __init__(self, db: AgentDB, wal_path: str = "wal_log.txt", hash_cache_path: str = ".memory_hashes.json"):
        """Initializes the MemoryBridge.

        Args:
            db: Associated AgentDB.
            wal_path: Write-ahead logging file path.
            hash_cache_path: Hash cache storage path.
        """
        self.db = db
        self.wal_path = wal_path
        self.hash_cache_path = hash_cache_path
        self.imported_hashes = self._load_hashes()

    def _load_hashes(self) -> Set[str]:
        """Loads processed file hashes from cache."""
        if os.path.exists(self.hash_cache_path):
            try:
                with open(self.hash_cache_path, "r", encoding="utf-8") as f:
                    return set(json.load(f))
            except Exception as e:
                logger.warning("Failed to load memory hashes cache: %s", e)
        return set()

    def _save_hashes(self) -> None:
        """Saves current hash cache to file."""
        try:
            with open(self.hash_cache_path, "w", encoding="utf-8") as f:
                json.dump(list(self.imported_hashes), f, indent=2)
        except Exception as e:
            logger.error("Failed to save memory hashes cache: %s", e)

    def _log_wal(self, action: str, namespace: str, key: str, details: str = "") -> None:
        """Writes an operation entry to the WAL file."""
        try:
            log_line = f"{time.time():.4f} | {action} | {namespace} | {key} | {details}\n"
            with open(self.wal_path, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception as e:
            logger.error("Failed to write to WAL log: %s", e)

    def import_file(self, file_path: str, namespace: str, embed_dim: int = 8) -> bool:
        """Reads a file line-by-line, embeds contents, and inserts into DB.

        Args:
            file_path: Source file path.
            namespace: Destination DB namespace.
            embed_dim: Embeddings output dimension size.

        Returns:
            True if the file was processed, False if it was already imported.
        """
        if not os.path.exists(file_path):
            logger.warning("File to import does not exist: %s", file_path)
            return False

        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        file_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if file_hash in self.imported_hashes:
            return False

        lines = content.splitlines()
        for idx, line in enumerate(lines):
            line_stripped = line.strip()
            if not line_stripped:
                continue

            vec = embed_text(line_stripped, dimension=embed_dim)
            key = f"{file_path}#L{idx+1}"
            metadata = {
                "file_path": file_path,
                "line_number": idx + 1,
                "content": line_stripped
            }

            self.db.insert(
                namespace=namespace,
                key=key,
                vector=vec,
                metadata=metadata
            )
            self._log_wal("INSERT", namespace, key, line_stripped)

        self.imported_hashes.add(file_hash)
        self._save_hashes()
        return True

    def prune_file(self, file_path: str, max_lines: int = 180) -> None:
        """Reduces the size of a text file, keeping lines with highest confidence scores.

        Args:
            file_path: Path of file to prune.
            max_lines: Upper limit of lines to retain.
        """
        if not os.path.exists(file_path):
            return

        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        if len(lines) <= max_lines:
            return

        scored_lines = []
        for idx, line in enumerate(lines):
            match = re.search(r"(?:score|confidence|weight)[:=]\s*([0-9.]+)", line, re.IGNORECASE)
            score = 1.0
            if match:
                try:
                    score = float(match.group(1))
                except ValueError:
                    pass
            scored_lines.append((score, idx, line))

        scored_lines.sort(key=lambda x: (-x[0], -x[1]))
        kept_scored = scored_lines[:max_lines]
        kept_scored.sort(key=lambda x: x[1])

        pruned_lines = [item[2] for item in kept_scored]

        tmp_path = file_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.writelines(pruned_lines)
        os.replace(tmp_path, file_path)

    def add_and_prune(self, file_path: str, new_lines: List[str], max_lines: int = 180) -> None:
        """Appends new lines to a file and prunes it to max_lines.

        Args:
            file_path: Target file path.
            new_lines: List of line strings to append.
            max_lines: Max capacity count of the file.
        """
        dir_path = os.path.dirname(file_path)
        if dir_path:
            os.makedirs(dir_path, mode=0o700, exist_ok=True)

        with open(file_path, "a", encoding="utf-8") as f:
            for line in new_lines:
                if not line.endswith("\n"):
                    line += "\n"
                f.write(line)

        self._log_wal("PRUNE_APPEND", "context", file_path, f"added {len(new_lines)} lines")
        self.prune_file(file_path, max_lines=max_lines)


class RetrievalFailureLog:
    """Self-tuning retrieval diagnosis (EvolveMem, research.google survey,
    July 2026 topic-page batch): EvolveMem reads per-query failure logs,
    root-causes retrieval misses, and proposes config changes to the
    retrieval mechanism itself (not just the stored content), with
    automatic revert-on-regression.

    Scoped down to what SmartRetrievalPipeline/HNSWIndex can actually
    adjust today: a single scalar retrieval threshold. Not wired into
    SmartRetrievalPipeline.retrieve() -- a caller records outcomes here
    after each retrieval + downstream result, then periodically calls
    suggest_threshold() to get an adjusted value, applies it behind its
    own shadow/canary check, and calls record_outcome for the new
    threshold's own results so a regression naturally pulls the average
    back down on the next suggest_threshold() call (the "revert" is a
    property of always weighting toward what recently worked, not a
    separate rollback mechanism).
    """

    def __init__(self, window: int = 50) -> None:
        self.window = window
        self._outcomes: List[bool] = []  # True = retrieval led to a useful result

    def record_outcome(self, useful: bool) -> None:
        self._outcomes.append(useful)
        self._outcomes = self._outcomes[-self.window :]

    @property
    def failure_rate(self) -> float:
        if not self._outcomes:
            return 0.0
        return 1.0 - (sum(1 for o in self._outcomes if o) / len(self._outcomes))

    def suggest_threshold(
        self, current_threshold: float, step: float = 0.05, min_samples: int = 10
    ) -> float:
        """Proposes a new similarity/match threshold based on recent
        failure rate. High failure rate (too many useless results getting
        through) raises the threshold to be more selective; very low
        failure rate with enough samples nudges it back down to avoid
        over-filtering. Returns current_threshold unchanged until
        min_samples have been recorded -- no tuning on thin evidence.
        """
        if len(self._outcomes) < min_samples:
            return current_threshold
        if self.failure_rate > 0.4:
            return min(1.0, current_threshold + step)
        if self.failure_rate < 0.1:
            return max(0.0, current_threshold - step)
        return current_threshold


def format_retro_report(state: Optional[Dict[str, Any]] = None) -> str:
    """Formats a retrospective report from the learning state as a markdown string.

    Args:
        state: Optional pre-loaded state dict. If None, loads from disk.

    Returns:
        Multi-line markdown string with full retrospective analysis.
    """
    if state is None:
        state = get_learning_state()

    sessions = state.get("sessions_distilled", 0)
    avg_cycles = state.get("avg_cycles_needed", 0.0)
    success_rate = state.get("success_rate", 0.0)
    avg_cycle_time = state.get("average_cycle_time", 0.0)
    vuln_count = state.get("vulnerability_counts", 0)
    last_roster = state.get("last_approved_roster", [])
    distillation_log = state.get("distillation_log", [])

    lines = [
        "# ASG Retrospective Report",
        "",
        "## Summary Metrics",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Sessions distilled | {sessions} |",
        f"| Average cycles/session | {avg_cycles:.2f} |",
        f"| Success rate | {success_rate:.1%} |",
        f"| Average cycle time | {avg_cycle_time:.1f}s |",
        f"| Vulnerability events | {vuln_count} |",
        f"| Last approved roster | {', '.join(last_roster) if last_roster else 'N/A'} |",
        "",
    ]

    # Patterns
    patterns = [e.get("pattern", "") for e in distillation_log if e.get("pattern")]
    lines.append("## Recent Patterns (What Worked)")
    if patterns:
        for p in patterns[-10:]:
            lines.append(f"- {p}")
    else:
        lines.append("_No patterns recorded yet._")
    lines.append("")

    # Anti-patterns
    raw_anti = [e.get("anti_pattern", "") for e in distillation_log if e.get("anti_pattern")]
    from collections import Counter
    anti_counts = Counter(p for p in raw_anti if p)
    lines.append("## Anti-Patterns (What to Avoid)")
    if anti_counts:
        for ap, count in anti_counts.most_common(5):
            lines.append(f"- ({count}x) {ap}")
    else:
        lines.append("_No anti-patterns recorded yet._")
    lines.append("")

    # Roster evolution
    lines.append("## Roster Evolution (Last 10 Sessions)")
    roster_log = distillation_log[-10:]
    if roster_log:
        lines.append("| Session | Roster | Cycles | Temperature |")
        lines.append("|---------|--------|--------|-------------|")
        total = len(distillation_log)
        for i, entry in enumerate(roster_log):
            idx = total - len(roster_log) + i + 1
            roster_str = ", ".join(entry.get("roster", []))
            cycles = entry.get("cycles_needed", "?")
            temp = entry.get("final_temperature", 0.0)
            lines.append(f"| #{idx} | {roster_str} | {cycles} | {temp:.2f} |")
    else:
        lines.append("_No sessions recorded yet._")
    lines.append("")

    # Recommendations
    lines.append("## Recommendations")
    recs = []
    if avg_cycles > 3:
        recs.append(
            f"🔥 **High avg cycles ({avg_cycles:.1f})**: Review initial roster selection — "
            f"personas may be misaligned with typical tasks. Consider pre-seeding rosters "
            f"from prior approved lists."
        )
    if vuln_count > 0:
        recs.append(
            f"🔒 **{vuln_count} security event(s)**: The security matrix scaling factor has "
            f"been tuned up. Review flagged payloads and consider adding more security "
            f"personas to default rosters."
        )
    if success_rate < 0.9 and sessions > 5:
        recs.append(
            f"⚠️ **Low success rate ({success_rate:.1%})**: Review error logs for permanent "
            f"vs transient failures. Consider increasing consensus_buffer_limit in config.yaml."
        )
    if not recs:
        recs.append("✅ All metrics healthy — no action needed.")
    for r in recs:
        lines.append(f"- {r}")
    lines.append("")

    if sessions == 0:
        lines.append("> **No sessions distilled yet.** Run a succession to build history.")

    return "\n".join(lines)
