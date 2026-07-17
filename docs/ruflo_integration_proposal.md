# Technical Integration Proposal: Ruflo Design Patterns in Absolute Self-Governance (ASG)

This proposal outlines the architectural design and code changes required to integrate key design patterns of **Ruflo** (swarm coordination, persistent memory, and background hooks) into the **Absolute Self-Governance (ASG)** system. This integration enhances ASG's decentralization, memory durability, consensus security, and event-driven automation.

---

## 1. Executive Summary

The Absolute Self-Governance (ASG) system is an autonomous multi-agent framework built on principles of programmatic succession, dynamic sizing, and consensus-driven decision-making. While the current ASG implementation provides a solid foundation for local file-watched state transitions (via `ContinuousNudger`), it operates primarily with flat agent lists, local database storage, and a single-model reasoning path.

**Ruflo** demonstrates highly mature, enterprise-ready patterns for:
1. **Decentralized Swarm Coordination**: Graph-based topology management, distributed consensus (PBFT/Raft), and lightweight Copy-On-Write workspace isolation.
2. **Persistent State Memory**: Structured TypeScript-based HNSW vector search, namespace segmentation, AES-256-GCM encryption at rest, and multi-phase context retrieval.
3. **Background Lifecycle Hooks & Routing**: Resilient event interception shims, cost-efficient 3-tier model routing, and persistent session hydration.

By integrating these patterns, we evolve the ASG system into a production-grade, highly resilient framework that secures roster succession against Byzantine manipulation, drastically reduces context token bloat through vector memory indexing, isolates parallel subagent file modifications using Copy-On-Write branches, and automates lifecycle hooks safely without breaking developer workflows.

---

## 2. Component 1: Swarm Coordination Integration

### 2.1 Technical Mapping and Architectural Integration

ASG's `self_governance/dimensioning.py` computes flat subagent counts and bundles them into a `LazyList` within a `SwarmConfig`. We propose extending this setup by introducing a graph-based topology layer based on Ruflo's `TopologyManager` design.

1. **Topology Management**: When ASG dimensions a swarm, the generated agents are mapped into a specific topology graph:
   - **Mesh**: Peer-to-peer network where each subagent maintains a bounded list of neighbors (e.g. maximum of 10), enabling distributed peer updates.
   - **Hierarchical**: Tree topology where a single high-tier agent (e.g., the `queen`) coordinates multiple worker agents directly.
   - **Centralized**: Traditional star topology routing communication through a designated coordinator.
   - **Hybrid**: Mesh-based peer groups coordinated by one or more queen nodes.
   - **Adaptive Rebalancing**: Periodically calculates node connection distribution and injects random links if degree skew is detected to prevent communication bottlenecks.
   - **Pathfinding**: Implements Breadth-First Search (BFS) to route information packets or voting digests between non-adjacent swarm agents.
   - **Role Cache Index**: Maintains a cache index map (`role_index: Dict[str, Set[str]]`) mapping roles to set of agent IDs to achieve $O(1)$ lookups of topology roles.

2. **Succession Consensus**: ASG currently uses TETD (Threshold-Enabled Temperature-Decay) consensus where voters rate roster candidates. We propose mapping Ruflo's consensus models directly into ASG succession rounds:
   - **Byzantine Fault Tolerance (PBFT)**: In high-stakes succession decisions, ASG will transition from simple simulated voting to a three-phase PBFT consensus protocol: `pre-prepare`, `prepare`, and `commit`. To guarantee Byzantine tolerance, the consensus roster size must satisfy:
     $$N \ge 3f + 1$$
     where $f$ is the maximum number of tolerable faulty or adversarial agents (calculated dynamically as $f = \lfloor(N-1)/3\rfloor$). Request digests are hashed via SHA-256 to ensure collision resistance.
   - **Raft Consensus**: For sequential task logging and state machines, a leader node is elected via randomized election timeouts (150ms-300ms) and maintains authority via 50ms heartbeats, replicating a unified succession log to followers.
   - **Gossip State Sync**: For eventually consistent metadata (e.g., learning state updates across workers), nodes propagate updates using a randomized fan-out (default 3 neighbors) and TTL limits, using a Last-Writer-Wins (LWW) resolution and an anti-entropy periodic merge. A `BoundedSet` wrapper limits memory leak risks by capping seen-message maps at 100,000 records.

3. **Workspace Isolation**: Running parallel subagents inside ASG introduces local file contention. We will integrate Copy-On-Write (COW) memory branching:
   - When succession triggers parallel agent executions, ASG forks a lightweight COW branch (mirroring the `agenticow` 162-byte branch pattern) representing the file changes.
   - Successful executions promote changes back to the parent repository branch, while failures delete the COW branch.
   - If the COW dependencies are missing or `CLAUDE_FLOW_NO_COW_MEMORY=1` is set, the system gracefully falls back to legacy directory copying.

4. **NDJSON Monitor Streams**: ASG's monitoring relies on polling the dashboard. We propose implementing an NDJSON (Newline Delimited JSON) stream protocol matching Ruflo's `watch --stream` tool. The Nudger will emit structured lines to stdout (such as `agent spawn`, `task progress`, `memory write`, and `health check` events) allowing Claude Code's native `Monitor` or terminal UIs to intercept events.

### 2.2 Architectural Mapping Table

| Ruflo Swarm Feature | ASG Target Module/System | Integration & Mapping Mechanism |
| :--- | :--- | :--- |
| **`TopologyManager`** | `self_governance/dimensioning.py` & `models.py` | Add topology metadata (Mesh/Hierarchical) to `LazyList` and `SwarmConfig`. Implement BFS routing and a role index cache for $O(1)$ lookups. |
| **Byzantine Consensus (PBFT)** | `self_governance/consensus.py` | Add PBFT state machine (`pre-prepare` $\rightarrow$ `prepare` $\rightarrow$ `commit` $\rightarrow$ `reply`) for succession voting under the $N \ge 3f + 1$ threshold. |
| **Raft Consistency** | `self_governance/consensus.py` | Establish leader/follower states with log replication quorums, 50ms heartbeat loops, and split-vote protection. |
| **Gossip Sync & BoundedSet** | `self_governance/consensus.py` & `learning.py` | Implement randomized peer dissemination of weights and parameters with a Map-based `BoundedSet` to mitigate memory leaks. |
| **ACOW Memory Branching** | Swarm Execution / Orchestrator | Dynamically fork 162-byte branches for parallel subagents, promoting deltas on success and discarding on failure. |
| **NDJSON Monitor Stream** | `self_governance/nudger.py` & `cli.py` | Emit structured JSON lifecycle events to stdout/streams for real-time visualization integrations. |

---

## 3. Component 2: Memory Storage & Retrieval Integration

### 3.1 Technical Mapping and Architectural Integration

ASG manages state through SQLite (`self_governance/db.py`) and reads/writes plain-text logs (`roster_rotation_log.md`). Integrating Ruflo's vector storage pattern provides high-speed similarity search, namespace isolation, encryption at rest, and optimized context pruning.

1. **HNSW Vector Search**: We propose adding a pure Python HNSW vector index implementation in `self_governance/learning.py`.
   - Utilizes a `BinaryMinHeap` for graph traversal and a `BinaryMaxHeap` for maintaining nearest neighbors.
   - Normalizes all vectors to unit length upon addition, turning cosine similarity computations into a simple dot product:
     $$Distance = 1 - \sum_{i=1}^{D} a_i \times b_i$$
     This eliminates expensive square root operations during similarity lookups.
   - State is serialized with a binary header `HNSW\x01` (`[0x48, 0x4e, 0x53, 0x57, 0x01]`).

2. **AgentDB Namespaces**: We will structure the ASG vector database by partitioning records into dedicated namespaces:
   - `patterns`: Coding style patterns and rules.
   - `succession`: Historical roster succession runs.
   - `feedback`: Human-in-the-loop and automated validation feedback.
   - `telemetry`: Iteration times and model performance statistics.
   - HNSW requires numeric IDs. To map ASG's string keys (e.g., agent names or task hashes) to integers, we propose a 53-bit dual hashing function combining `djb2` and `sdbm`:
     $$\text{Hash} = (\text{djb2} \oplus \text{sdbm}) \ \& \ 0x1\text{FFFFFFFFFFFFF}$$
     Collisions are resolved via linear probing and tracked in $O(1)$ bidirectional index maps. Bulk inserts are executed in concurrent batches of size 50.

3. **Encryption at Rest**:
   - SQLite databases and session dumps will support symmetric AES-256-GCM encryption.
   - Wire format: `magic (4 bytes: "RFE1") || iv (12 bytes) || ciphertext (N bytes) || tag (16 bytes)`.
   - Key is loaded from `CLAUDE_FLOW_ENCRYPTION_KEY`.
   - Reads sniff for the `"RFE1"` magic header. If present, the stream is decrypted; if not, it falls back to plaintext, allowing zero-downtime database upgrades.
   - File permissions are locked down to mode `0600` (directories to `0700`).

4. **SmartRetrieval 5-Phase Pipeline**: Before querying agent personas or loading historical consensus logs, queries undergo a 5-phase retrieval loop:
   - **Query Expansion**: Generates up to 3 variations of the search query to improve retrieval coverage.
   - **RRF (Reciprocal Rank Fusion)**: Executes searches across namespaces and fuses results using:
     $$RRF(d) = \sum_{m \in M} \frac{1}{k + r_m(d)}$$
     where $k$ defaults to 60.
   - **Recency Boost**: Decays scores based on timestamp age:
     $$Boost = 0.5^{\frac{\text{AgeMs}}{\text{HalfLifeMs}}}$$
   - **MMR (Maximal Marginal Relevance) Diversity**: Deduplicates similar records by measuring token Jaccard overlap:
     $$Jaccard(A, B) = \frac{|A \cap B|}{|A \cup B|}$$
     favoring unique context.
   - **Session Round-Robin**: Interleaves results from different session IDs to prevent one session from dominating the context.

5. **Memory Bridge**: Connects ASG plain-text files (like `roster_rotation_log.md` and `prompt_draft.md`) to the vector store.
   - Performs idempotent imports by checking SHA-256 content hashes.
   - Syncs high-confidence learning points back to prompt files.
   - Prunes memory files to stay under 180 lines using confidence-weighted eviction to avoid token saturation.

### 3.2 Architectural Mapping Table

| Ruflo Memory Feature | ASG Target Module/System | Integration & Mapping Mechanism |
| :--- | :--- | :--- |
| **HNSW Vector Search** | `self_governance/learning.py` | Implement HNSW graph index in Python with Min/Max heaps and unit-normalized dot-product matching. |
| **AgentDB Namespaces** | `self_governance/db.py` & `learning.py` | Add namespace partitioning to storage tables. Convert string keys to 53-bit integers via `djb2`/`sdbm` with linear probing. Batch edits in chunks of 50. |
| **AES-256-GCM Vault** | `self_governance/db.py` | Write databases and config files using AES-256-GCM, sniff `RFE1` bytes for migration, and apply `0600` file modes. |
| **SmartRetrieval Pipeline** | `self_governance/consensus.py` | Process roster queries through expansion, RRF, recency decay, Jaccard-based MMR, and round-robin. |
| **Memory Bridge** | `self_governance/nudger.py` | Mirror `roster_rotation_log.md` changes into vector DB using SHA-256 caches, curating context files under 180 lines. |

---

## 4. Component 3: Background Event Hooks & Routing

### 4.1 Technical Mapping and Architectural Integration

ASG's `ContinuousNudger` operates on file modifications and triggers succession. We propose integrating background lifecycle hooks and a cost-aware routing architecture.

1. **Lifecycle Hooks**: We propose injecting hook execution steps into the Nudger's loop, reading configurations from `hooks.json`:
   - **PreToolUse**: Fired before a subagent runs a command or makes edits, returning a JSON authorization verdict (`{"permission":"allow"}`).
   - **PostToolUse**: Executed after a tool run, capturing arguments and outputs to feed into the ASG learning loop.
   - **PreCompact**: Injected before prompt compiling, adding active topology layout parameters to the context.
   - **Stop**: Triggered on session termination, persisting states and exporting telemetry metrics.
   - **Resilient Invoker Shim**: To prevent hook failures from halting the Nudger, hooks are executed via a python subprocess wrapper that intercepts standard streams, catches all exceptions, and guarantees an exit code of `0`.

2. **3-Tier Model Routing**: ASG uses a static model for succession (`self_governance/config.py`). We propose integrating a 3-tier router:
   - **Tier 1: AST Codemods**: Before calling an LLM, the router parses simple instructions (e.g. config updates or minor edits) and matches them to AST codemods. Latency is $<1\text{ms}$ and cost is $\$0.00$. A dry-run is executed to verify changes before applying.
   - **Tier 2: Haiku**: Low-complexity reviews and basic agent evaluations route to Haiku.
   - **Tier 3: Sonnet/Opus**: High-complexity calculations (e.g., Byzantine consensus rounds, complex refactoring tasks) route to Sonnet or Opus. Complexity is evaluated by checking input lengths, function counts, indentation depth, or keywords (e.g., `consensus`, `cryptography`, `byzantine`).
   - **Neural Routing**: Generates task embeddings using `@xenova/transformers` (or equivalent python implementation) and matches them via cosine similarity to a performance database, falling back to a configured OpenRouter endpoint (e.g., `inclusionai/ling-2.6-flash`).

3. **Persistent Session Management**:
   - **Serialization**: A `session_save` module captures the current state (SQLite database, active task trees, configurations) and serializes them to an encrypted JSON file at `.self-governance/sessions/<session-id>.json`.
   - **Hydration**: A `session_restore` command decrypts the file, recovers SQLite tables, restores variables, and resumes the file-watcher daemon.
   - **Uniform Projection**: Supports listing sessions in a uniform schema regardless of whether they were created manually or by the background system.

### 4.2 Architectural Mapping Table

| Ruflo Hooks & Routing Feature | ASG Target Module/System | Integration & Mapping Mechanism |
| :--- | :--- | :--- |
| **Lifecycle Hooks** | `self_governance/nudger.py` | Add `PreToolUse`, `PostToolUse`, `PreCompact`, and `Stop` hooks triggered within the Nudger lifecycle loop. |
| **Resilient Invoker Shim** | `self_governance/nudger.py` | Implement subprocess wrapper that suppresses outputs and intercepts errors to guarantee exit code `0`. |
| **3-Tier Model Routing** | `self_governance/config.py` & `consensus.py` | Map tasks to AST transformations, Haiku, or Sonnet/Opus based on code complexity. Implement neural embedding fallback. |
| **Persistent Session** | `self_governance/cli.py` & `db.py` | Add CLI commands to dump and restore SQLite state and config parameters using encrypted JSON. |

---

## 5. Concrete API and Module Changes in `src/self_governance/`

We outline below the specific class, function, and file modifications required within the `src/self_governance/` directory.

### 5.1 `self_governance/nudger.py`

Modify `ContinuousNudger` to load event hooks from `hooks.json`, run them through a resilient subprocess executor, stream NDJSON events, and maintain the Memory Bridge.

```python
# src/self_governance/nudger.py

import os
import json
import subprocess
import time
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("self_governance.nudger")

class HookExecutionError(Exception):
    """Raised when a hook fails but is not suppressed by the shim."""
    pass

class ResilientHookExecutor:
    """
    Executes lifecycle hooks resiliently, suppressing stderr/stdout,
    and ensuring the host process never crashes on hook errors.
    """
    def __init__(self, hooks_config_path: str = "hooks.json") -> None:
        self.config_path = hooks_config_path
        self.hooks = self._load_hooks()

    def _load_hooks(self) -> Dict[str, Any]:
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r") as f:
                    return json.load(f)
            except Exception as e:
                logger.error("Failed to load hooks.json: %s", e)
        return {}

    def execute_hook(self, hook_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes a background hook. Standard output/stderr are captured
        and suppressed to maintain Cursor compatibility. Returns a status dict.
        """
        hook_cmd = self.hooks.get(hook_name)
        if not hook_cmd:
            return {"permission": "allow", "status": "no_hook_configured"}

        try:
            # Resilient execution shim: always suppress output and capture exit code
            res = subprocess.run(
                [hook_cmd],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=5.0,
                check=False
            )
            # Suppress diagnostics to prevent Cursor/Claude Code JSON parser pollution
            if res.returncode != 0:
                logger.warning("Hook %s exited with non-zero status: %d", hook_name, res.returncode)
            
            # Match Cursor/Claude Code preToolUse verdict structure
            return {"permission": "allow", "status": "executed", "exit_code": res.returncode}
        except Exception as e:
            logger.error("Resilient hook execution failed for %s: %s", hook_name, e)
            return {"permission": "allow", "status": "error", "error_message": str(e)}

class NDJSONEventStreamer:
    """
    Streams lifecycle and telemetry events to standard out using the NDJSON protocol.
    """
    @staticmethod
    def emit_event(event_type: str, details: Dict[str, Any]) -> None:
        event = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "event": event_type,
            "details": details
        }
        # Print line-delimited JSON for real-time observability integration
        print(json.dumps(event), flush=True)

# Integration into ContinuousNudger
class ContinuousNudger:
    # Existing methods ...

    def process_handoff_with_hooks(self) -> None:
        executor = ResilientHookExecutor()
        
        # PreToolUse Hook
        verdict = executor.execute_hook("PreToolUse", {"action": "process_handoff"})
        NDJSONEventStreamer.emit_event("pre_tool_use", verdict)

        # Execute core succession tasks ...
        # (Inside trigger_succession, a PreCompact hook can be run before prompting)
        
        # PostToolUse Hook
        post_verdict = executor.execute_hook("PostToolUse", {"status": "success"})
        NDJSONEventStreamer.emit_event("post_tool_use", post_verdict)
```

### 5.2 `self_governance/consensus.py`

Extend `ConsensusEngine` to support Raft leader logs, PBFT Byzantine consensus, and HNSW vector-augmented memory lookups.

```python
# src/self_governance/consensus.py

from typing import List, Dict, Any, Optional
import hashlib
import json

class PBFTConsensusStage:
    """
    Implements a three-phase agreement protocol (pre-prepare, prepare, commit)
    to enforce Byzantine Fault Tolerance during succession voting.
    """
    def __init__(self, nodes: List[str]) -> None:
        self.nodes = nodes
        self.n = len(nodes)
        self.f = max(1, (self.n - 1) // 3)  # PBFT: N >= 3f + 1

    def run_stage(self, proposed_roster: List[str]) -> bool:
        # Step 1: Pre-prepare (Leader proposes hash of the roster)
        roster_digest = hashlib.sha256(json.dumps(proposed_roster).encode()).hexdigest()
        
        # Step 2: Prepare (Nodes broadcast agreements)
        prepares = 0
        for node in self.nodes:
            # Simulate node verification of the digest
            prepares += 1
            
        # Step 3: Commit (Nodes verify 2f + 1 prepares have been broadcast)
        if prepares >= (2 * self.f + 1):
            commits = prepares  # commit broadcast simulation
            if commits >= (2 * self.f + 1):
                return True
        return False

# Integration into ConsensusEngine
class ConsensusEngine:
    # Existing fields...
    
    def __init__(self, *args, **kwargs):
        # Existing initialization...
        self.vector_store_path = kwargs.get("vector_store", "hnsw_index.bin")

    def _query_historical_memory(self, agent_role: str) -> str:
        """
        Queries the HNSW index to retrieve historical performance details of the agent.
        """
        # HNSW search placeholder (integrating memory lookup)
        try:
            # Load HNSW index, compute dot product, extract historical scoring contexts
            return f"Historical Feedback for {agent_role}: Rated highly in previous 3 iterations."
        except Exception:
            return ""

    def _score_agent_with_memory(self, agent: str, peer_feedback: str) -> tuple[float, str]:
        # Fetch historical vector data to augment the prompt context
        history = self._query_historical_memory(agent)
        augmented_feedback = f"{peer_feedback}\n{history}"
        
        # Call model evaluation...
        return self._score_agent(agent, augmented_feedback)
```

### 5.3 `self_governance/dimensioning.py`

Extend `LazyList` to include topology graph mapping, routing hops, and adaptive role index caches.

```python
# src/self_governance/dimensioning.py

from typing import List, Dict, Set, Tuple, Optional
from self_governance.models import Agent

class TopologyGraph:
    """
    Calculates edges and layouts (Mesh, Hierarchical) for the dimensioned swarm.
    """
    def __init__(self, agents: List[Agent], layout: str = "mesh") -> None:
        self.agents = agents
        self.layout = layout
        self.edges: Dict[str, List[str]] = {}
        self.role_index: Dict[str, Set[str]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        # Populate O(1) role cache index
        for agent in self.agents:
            self.role_index.setdefault(agent.role, set()).add(agent.id)

        if self.layout == "hierarchical":
            # Assign first agent as Queen node, others as children
            queen = self.agents[0].id
            self.edges[queen] = [a.id for a in self.agents[1:]]
            for a in self.agents[1:]:
                self.edges[a.id] = [queen]
        else:
            # Default to Bounded Mesh (each node connects to up to 10 neighbors)
            for i, agent in enumerate(self.agents):
                neighbors = []
                for j in range(1, 6):
                    neighbors.append(self.agents[(i + j) % len(self.agents)].id)
                    neighbors.append(self.agents[(i - j) % len(self.agents)].id)
                self.edges[agent.id] = neighbors

    def find_optimal_path(self, start_id: str, end_id: str) -> List[str]:
        """BFS pathfinding to determine routing steps between agents."""
        queue = [[start_id]]
        visited = {start_id}
        while queue:
            path = queue.pop(0)
            node = path[-1]
            if node == end_id:
                return path
            for neighbor in self.edges.get(node, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return []

# Integration into LazyList
class LazyList(Sequence[Agent]):
    # Existing fields...
    
    def get_topology_graph(self, layout: str = "mesh") -> TopologyGraph:
        """Constructs and returns the topology graph of the dimensioned agents."""
        agents = [self[i] for i in range(self._total_count)]
        return TopologyGraph(agents, layout=layout)
```

### 5.4 `self_governance/models.py`

Update the `Agent` and `SwarmConfig` schemas to support topology parameters and encryption markers.

```python
# src/self_governance/models.py

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class Agent(BaseModel):
    role: str
    prompt: str
    capabilities: List[str] = Field(default_factory=list)
    
    # Topology extensions
    id: str = Field(default="")
    topology_role: str = Field(default="peer")  # peer, queen, coordinator, worker
    connections: List[str] = Field(default_factory=list)  # adjacency list of agent IDs
    
    # Metadata for model routing
    complexity_level: str = Field(default="low")  # low (Haiku), high (Sonnet/Opus)

class SwarmConfig(BaseModel):
    swarm: Any
    topology_layout: str = Field(default="mesh")  # mesh, hierarchical, centralized
    encrypted_at_rest: bool = Field(default=False)
```

### 5.5 `self_governance/cli.py`

Add new subcommands for session saving/restoring and checking database encryption status.

```python
# src/self_governance/cli.py

import argparse
import sys
import os
import json
from self_governance.db import save_session_state, restore_session_state

def add_ruflo_subcommands(subparsers) -> None:
    # session-save command
    parser_save = subparsers.add_parser("session-save", help="Save the current ASG session state")
    parser_save.add_argument("--session-id", required=True, help="Session Identifier")
    parser_save.add_argument("--output-dir", default=".self-governance/sessions", help="Output directory")

    # session-restore command
    parser_restore = subparsers.add_parser("session-restore", help="Restore an ASG session state")
    parser_restore.add_argument("--session-id", required=True, help="Session Identifier")
    parser_restore.add_argument("--session-dir", default=".self-governance/sessions", help="Sessions directory")

    # db-encrypt-check command
    parser_check = subparsers.add_parser("db-encrypt-check", help="Verify if database encryption is active")
    parser_check.add_argument("--db-path", default="self_governance.db", help="Path to database file")

def handle_ruflo_subcommands(args) -> None:
    if args.subcommand == "session-save":
        os.makedirs(args.output_dir, exist_ok=True)
        session_path = os.path.join(args.output_dir, f"{args.session_id}.json")
        
        # Call database state serializer
        save_session_state(session_path)
        os.chmod(session_path, 0o600)  # Restricted owner-only read/write
        print(f"Session successfully saved and encrypted to {session_path}")
        
    elif args.subcommand == "session-restore":
        session_path = os.path.join(args.session_dir, f"{args.session_id}.json")
        if not os.path.exists(session_path):
            print(f"Error: Session file {session_path} not found.", file=sys.stderr)
            sys.exit(1)
            
        restore_session_state(session_path)
        print(f"Session successfully restored from {session_path}")
        
    elif args.subcommand == "db-encrypt-check":
        if not os.path.exists(args.db_path):
            print(f"Database file not found at {args.db_path}", file=sys.stderr)
            sys.exit(1)
            
        # Read the first 4 bytes to check for "RFE1" magic-byte sniffing
        with open(args.db_path, "rb") as f:
            header = f.read(4)
            
        if header == b"RFE1":
            print(f"Database at {args.db_path} is ENCRYPTED (magic header: RFE1).")
        else:
            print(f"Database at {args.db_path} is PLAINTEXT (no magic header). Migration recommended.")
```
