import json
import pytest
from unittest.mock import patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from self_governance.topology import SwarmTopology
from self_governance.consensus import PBFTConsensusEngine
from self_governance.p2p import EnhancedGossipProtocol, BoundedSet
from self_governance.db import SovereignMemory, COWMemoryBranch, Base
from self_governance.nudger import ContinuousNudger
from self_governance.config import OrchestratorConfig

# --- 1. Graph Topology Tests ---

def test_swarm_topology_mesh():
    nodes = ["nodeA", "nodeB", "nodeC"]
    topo = SwarmTopology("MESH", nodes)
    assert topo.topology_type == "MESH"
    assert len(topo.edges["nodeA"]) == 2
    assert "nodeB" in topo.edges["nodeA"]
    assert "nodeC" in topo.edges["nodeA"]

def test_swarm_topology_star():
    nodes = ["hub", "spoke1", "spoke2"]
    topo = SwarmTopology("STAR", nodes)
    assert topo.topology_type == "STAR"
    assert topo.edges["hub"] == {"spoke1", "spoke2"}
    assert topo.edges["spoke1"] == {"hub"}
    assert topo.edges["spoke2"] == {"hub"}

def test_swarm_topology_hierarchical():
    nodes = ["root", "left", "right", "left_left"]
    topo = SwarmTopology("HIERARCHICAL", nodes)
    assert topo.topology_type == "HIERARCHICAL"
    assert "left" in topo.edges["root"]
    assert "right" in topo.edges["root"]
    assert "left_left" in topo.edges["left"]

def test_swarm_topology_routing_bfs():
    nodes = ["A", "B", "C", "D"]
    topo = SwarmTopology("STAR", nodes)  # A is hub, B,C,D are spokes
    route = topo.find_route("B", "C")
    assert route == ["B", "A", "C"]
    assert topo.find_route("B", "Z") == []

def test_swarm_topology_role_caching():
    topo = SwarmTopology("MESH", ["A", "B"])
    topo.cache_roles(["developer", "tester", "coordinator"])
    assert topo.get_cached_role_index("developer") == 0
    assert topo.get_cached_role_index("tester") == 1
    assert topo.get_cached_role_index("coordinator") == 2
    assert topo.get_cached_role_index("unknown") is None

# --- 2. PBFT & Raft Log Consistency Tests ---

def test_pbft_consensus_engine_network_size():
    engine1 = PBFTConsensusEngine("node1", ["node2", "node3", "node4"], f=1)
    assert engine1.verify_network_size() is True

    engine2 = PBFTConsensusEngine("node1", ["node2", "node3"], f=1)
    assert engine2.verify_network_size() is False

def test_pbft_consensus_engine_state_transitions():
    engine = PBFTConsensusEngine("node1", ["node2", "node3", "node4"], f=1)
    assert engine.state == "Pre-prepared"

    assert engine.receive_pre_prepare("node2", term=1, index=1, message="hello") is True
    assert engine.state == "Prepared"

    assert engine.receive_pre_prepare("node2", term=0, index=1, message="old") is False

    assert engine.receive_prepare("node2", term=1, index=1, message="hello") is False
    assert engine.state == "Prepared"
    assert engine.receive_prepare("node3", term=1, index=1, message="hello") is True
    assert engine.state == "Committed"

    assert engine.receive_commit("node2", term=1, index=1, message="hello") is False
    assert engine.receive_commit("node3", term=1, index=1, message="hello") is False
    assert engine.receive_commit("node4", term=1, index=1, message="hello") is True

def test_raft_append_entries():
    initial_log = [
        {"term": 1, "index": 0, "command": "cmd1"},
        {"term": 1, "index": 1, "command": "cmd2"},
    ]
    engine = PBFTConsensusEngine("node1", [], f=0, log=list(initial_log))
    engine.current_term = 1

    success, match_index = engine.append_entries(
        term=0, leader_id="leader", prev_log_index=1, prev_log_term=1, entries=[], leader_commit=0
    )
    assert success is False

    success, match_index = engine.append_entries(
        term=1, leader_id="leader", prev_log_index=5, prev_log_term=1, entries=[], leader_commit=0
    )
    assert success is False

    success, match_index = engine.append_entries(
        term=1, leader_id="leader", prev_log_index=1, prev_log_term=2, entries=[], leader_commit=0
    )
    assert success is False

    new_entries = [
        {"term": 2, "index": 1, "command": "conflict_cmd"},
        {"term": 2, "index": 2, "command": "new_cmd"},
    ]
    success, match_index = engine.append_entries(
        term=2, leader_id="leader", prev_log_index=0, prev_log_term=1, entries=new_entries, leader_commit=2
    )
    assert success is True
    assert len(engine.log) == 3
    assert engine.log[1]["command"] == "conflict_cmd"
    assert engine.log[2]["command"] == "new_cmd"
    assert engine.commit_index == 2

# --- 3. Gossip Sync Tests ---

def test_bounded_set():
    bset = BoundedSet(max_size=3)
    bset.add("msg1")
    bset.add("msg2")
    bset.add("msg3")
    assert "msg1" in bset
    bset.add("msg4")
    assert "msg1" not in bset
    assert "msg4" in bset
    assert len(bset) == 3

def test_enhanced_gossip_protocol_ttl_and_loop():
    nodeA = EnhancedGossipProtocol("nodeA", max_seen_size=10, default_ttl=3)
    nodeB = EnhancedGossipProtocol("nodeB", max_seen_size=10, default_ttl=3)
    nodeC = EnhancedGossipProtocol("nodeC", max_seen_size=10, default_ttl=3)

    nodeA.register_peer("nodeB", nodeB)
    nodeB.register_peer("nodeA", nodeA)
    nodeB.register_peer("nodeC", nodeC)
    nodeC.register_peer("nodeB", nodeB)

    nodeA.publish_gossip("status", "active", version=1)
    assert nodeB.state["status"] == ("active", 1)
    assert nodeC.state["status"] == ("active", 1)

    nodeA_limited = EnhancedGossipProtocol("nodeA", max_seen_size=10, default_ttl=1)
    nodeB_limited = EnhancedGossipProtocol("nodeB", max_seen_size=10, default_ttl=1)
    nodeC_limited = EnhancedGossipProtocol("nodeC", max_seen_size=10, default_ttl=1)

    nodeA_limited.register_peer("nodeB", nodeB_limited)
    nodeB_limited.register_peer("nodeA", nodeA_limited)
    nodeB_limited.register_peer("nodeC", nodeC_limited)
    nodeC_limited.register_peer("nodeB", nodeB_limited)

    nodeA_limited.publish_gossip("role", "leader", version=1)
    assert nodeB_limited.state["role"] == ("leader", 1)
    assert "role" not in nodeC_limited.state

def test_enhanced_gossip_anti_entropy():
    nodeA = EnhancedGossipProtocol("nodeA")
    nodeB = EnhancedGossipProtocol("nodeB")

    nodeA.update_local_state("key1", "val1_v2", version=2)
    nodeA.update_local_state("key2", "val2_v1", version=1)

    nodeB.update_local_state("key1", "val1_v1", version=1)
    nodeB.update_local_state("key3", "val3_v3", version=3)

    nodeA.anti_entropy_merge(nodeB)

    assert nodeA.state["key1"] == ("val1_v2", 2)
    assert nodeB.state["key1"] == ("val1_v2", 2)
    assert nodeA.state["key3"] == ("val3_v3", 3)
    assert nodeB.state["key3"] == ("val3_v3", 3)
    assert nodeA.state["key2"] == ("val2_v1", 1)
    assert nodeB.state["key2"] == ("val2_v1", 1)

# --- 4. COW Memory Branching Tests ---

@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    yield db
    db.close()

def test_cow_memory_branch_isolation_and_merge(in_memory_db):
    parent = SovereignMemory()
    parent.set("shared_key", "original_value", "agent_1", db=in_memory_db)

    branch = COWMemoryBranch(parent_memory=parent, db=in_memory_db)
    assert branch.get("shared_key", "agent_1") == "original_value"

    branch.set("shared_key", "new_value", "agent_1")
    branch.set("branch_key", "isolated_value", "agent_1")

    assert parent.get("shared_key", "agent_1", db=in_memory_db) == "original_value"
    assert parent.get("branch_key", "agent_1", db=in_memory_db) is None

    assert branch.get("shared_key", "agent_1") == "new_value"
    assert branch.get("branch_key", "agent_1") == "isolated_value"

    success = branch.merge()
    assert success is True

    assert parent.get("shared_key", "agent_1", db=in_memory_db) == "new_value"
    assert parent.get("branch_key", "agent_1", db=in_memory_db) == "isolated_value"

def test_cow_memory_branch_fallback():
    branch = COWMemoryBranch(parent_memory=None, db=None)
    branch.set("key1", "val1", "agent_X")
    assert branch.get("key1", "agent_X") == "val1"
    
    assert branch.merge() is True
    assert branch.get("key1", "agent_X") == "val1"

# --- 5. NDJSON Events Tests ---

def test_ndjson_events_emitted(tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    config = OrchestratorConfig()
    config.config_data["watcher"]["handoff_file"] = ".planning/CURRENT_STATE.md"
    
    handoff_path = tmp_path / ".planning/CURRENT_STATE.md"
    handoff_path.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n  - agent_B", encoding="utf-8")

    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)
    
    class DummyConsensusResult:
        approved_roster = ["agent_A"]
        final_temperature = 1.2
        final_threshold = 8.5
        prompt_tokens = 100
        completion_tokens = 50
        cycles_needed = 2

    with patch("self_governance.nudger.run_consensus", return_value=DummyConsensusResult()), \
         patch("self_governance.complexity.calculate_ast_complexity", return_value=1000):
        nudger.process_handoff()

    ndjson_file = tmp_path / "monitoring_events.ndjson"
    assert ndjson_file.exists()

    events = []
    with open(ndjson_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))

    event_types = [e["type"] for e in events]
    assert "progress" in event_types
    assert "spawn" in event_types
    assert "consensus" in event_types

    consensus_event = next(e for e in events if e["type"] == "consensus")
    assert "timestamp" in consensus_event
    assert consensus_event["approved_roster"] == ["agent_A"]
    assert consensus_event["final_temperature"] == 1.2
    assert consensus_event["final_threshold"] == 8.5
