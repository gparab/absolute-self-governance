import os
import time
import math
import pytest
from unittest.mock import patch

from self_governance.learning import (
    HNSWIndex,
    djb2_hash,
    sdbm_hash,
    dual_hash,
    encrypt_data,
    decrypt_data,
    AgentDB,
    SmartRetrievalPipeline,
    MemoryBridge,
)

# ----------------------------------------------------------------------
# 1. HNSW INDEX TESTS
# ----------------------------------------------------------------------

def test_hnsw_normalization():
    v = [3.0, 4.0]
    normalized = HNSWIndex.normalize(v)
    assert math.isclose(normalized[0], 0.6)
    assert math.isclose(normalized[1], 0.8)
    assert math.isclose(sum(x * x for x in normalized), 1.0)


def test_hnsw_dot_product():
    v1 = [1.0, 0.0]
    v2 = [0.0, 1.0]
    assert math.isclose(HNSWIndex.dot_product(v1, v2), 0.0)

    v3 = [0.6, 0.8]
    assert math.isclose(HNSWIndex.dot_product(v3, v3), 1.0)


def test_hnsw_insertion_and_search():
    index = HNSWIndex(M=4, M0=8, efConstruction=10, efSearch=10)
    
    # Insert some 2D points
    index.insert(1, [1.0, 0.0], level=0)
    index.insert(2, [0.0, 1.0], level=1)
    index.insert(3, [0.707, 0.707], level=0)
    
    # Search closest to [0.9, 0.1]
    results = index.search([0.9, 0.1], k=2)
    
    assert len(results) >= 1
    # Node 1 should be closer than Node 2
    assert results[0][1] == 1


def test_hnsw_serialization_deserialization():
    index = HNSWIndex(M=4, M0=8, efConstruction=10, efSearch=10)
    index.insert(10, [1.0, 0.0], level=0)
    index.insert(20, [0.0, 1.0], level=1)
    index.insert(30, [0.707, 0.707], level=0)
    
    serialized = index.serialize()
    assert serialized.startswith(b"HNSW\x01")
    
    deserialized = HNSWIndex.deserialize(serialized)
    assert deserialized.M == index.M
    assert deserialized.M0 == index.M0
    assert deserialized.max_level == index.max_level
    assert deserialized.enter_node == index.enter_node
    assert len(deserialized.nodes) == len(index.nodes)
    
    # Search comparison
    res_orig = index.search([1.0, 1.0], k=2)
    res_deser = deserialized.search([1.0, 1.0], k=2)
    assert res_orig == res_deser


# ----------------------------------------------------------------------
# 2. KEY HASHING & AGENTDB NAMESPACES
# ----------------------------------------------------------------------

def test_key_hashing_to_53_bit():
    key = "agent_john_doe"
    
    h_djb2 = djb2_hash(key)
    h_sdbm = sdbm_hash(key)
    h_dual = dual_hash(key)
    
    assert h_djb2 <= 0x1FFFFFFFFFFFFF
    assert h_sdbm <= 0x1FFFFFFFFFFFFF
    assert h_dual <= 0x1FFFFFFFFFFFFF
    
    # Verify deterministic output
    assert djb2_hash(key) == h_djb2
    assert sdbm_hash(key) == h_sdbm
    assert dual_hash(key) == h_dual


def test_agent_db_namespaces_and_probing():
    db = AgentDB()
    
    # Verify namespaces exist
    for ns in ["patterns", "succession", "feedback", "telemetry"]:
        assert ns in db.namespaces
        
    # Test linear probing
    # Force identical hashes by patching dual_hash
    with patch("self_governance.learning.dual_hash", return_value=12345):
        id1 = db.insert("patterns", "key1", [1.0, 0.0])
        id2 = db.insert("patterns", "key2", [0.0, 1.0])
        
        assert id1 == 12345
        assert id2 == 12346  # Probed
        
        # Verify bidirectional maps
        assert db.key_to_id["patterns"]["key1"] == id1
        assert db.key_to_id["patterns"]["key2"] == id2
        assert db.id_to_key["patterns"][id1] == "key1"
        assert db.id_to_key["patterns"][id2] == "key2"


def test_agent_db_batch_insert():
    db = AgentDB()
    
    items = []
    for i in range(120):
        items.append({
            "key": f"item_{i}",
            "vector": [math.sin(i), math.cos(i)],
            "metadata": {"index": i}
        })
        
    ids = db.batch_insert("telemetry", items)
    assert len(ids) == 120
    assert len(db.namespaces["telemetry"].nodes) == 120


# ----------------------------------------------------------------------
# 3. ENCRYPTION AT REST TESTS
# ----------------------------------------------------------------------

def test_aes_gcm_encryption_nominal(monkeypatch):
    key_hex = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    monkeypatch.setenv("CLAUDE_FLOW_ENCRYPTION_KEY", key_hex)
    
    plaintext = b"Highly confidential swarm memories"
    ciphertext = encrypt_data(plaintext)
    
    assert ciphertext.startswith(b"RFE1")
    assert len(ciphertext) > len(plaintext) + 16
    
    decrypted = decrypt_data(ciphertext)
    assert decrypted == plaintext


def test_encryption_no_key_warning_and_value_error(monkeypatch):
    monkeypatch.delenv("CLAUDE_FLOW_ENCRYPTION_KEY", raising=False)
    
    # Nominal fallback if require_key is False
    plaintext = b"Some data"
    res = encrypt_data(plaintext, require_key=False)
    assert res == plaintext  # Unencrypted fallback
    
    # Should raise ValueError if require_key is True
    with pytest.raises(ValueError, match="CLAUDE_FLOW_ENCRYPTION_KEY is required"):
        encrypt_data(plaintext, require_key=True)
        
    # Decrypting encrypted data when key is missing should raise ValueError
    encrypted_data = b"RFE1_some_iv_and_ciphertext"
    with pytest.raises(ValueError, match="CLAUDE_FLOW_ENCRYPTION_KEY is not set"):
        decrypt_data(encrypted_data)


def test_decryption_fallback_parsing():
    # If ciphertext doesn't start with RFE1, return it as plaintext directly
    plaintext = b"raw unencrypted data"
    assert decrypt_data(plaintext) == plaintext


def test_db_save_and_load_with_permissions(tmp_path, monkeypatch):
    key_hex = "00112233445566778899aabbccddeeff00112233445566778899aabbccddeeff"
    monkeypatch.setenv("CLAUDE_FLOW_ENCRYPTION_KEY", key_hex)
    
    db = AgentDB()
    db.insert("feedback", "test_key", [1.0, 0.0], metadata={"feedback": "excellent"})
    
    db_file = tmp_path / "subdir" / "test_db.bin"
    db.save_to_file(str(db_file))
    
    # Verify file mode (0600 -> owner read/write only)
    assert os.path.exists(db_file)
    stat_info = os.stat(db_file)
    # Extract permission bits
    assert (stat_info.st_mode & 0o777) == 0o600
    
    # Verify parent directory mode (0700)
    parent_stat = os.stat(db_file.parent)
    assert (parent_stat.st_mode & 0o777) == 0o700
    
    # Load back and verify
    loaded_db = AgentDB.load_from_file(str(db_file))
    assert "test_key" in loaded_db.key_to_id["feedback"]
    assert loaded_db.records["feedback"][loaded_db.key_to_id["feedback"]["test_key"]].metadata["feedback"] == "excellent"


# ----------------------------------------------------------------------
# 4. 5-PHASE RETRIEVAL PIPELINE TESTS
# ----------------------------------------------------------------------

def test_5_phase_retrieval_pipeline():
    db = AgentDB()
    
    # Add items with different sessions, contents (Jaccard), and timestamps
    t_now = time.time()
    
    # Session 1 - Item 1 (New, highly relevant)
    db.insert(
        namespace="patterns",
        key="Agent design patterns in ASG",
        vector=[1.0, 0.0, 0.0],
        timestamp=t_now,
        metadata={"session_id": "session_1", "desc": "first"}
    )
    
    # Session 1 - Item 2 (New, very similar text -> should be suppressed by MMR Jaccard)
    db.insert(
        namespace="patterns",
        key="Agent design patterns in ASG duplicates",
        vector=[0.99, 0.01, 0.0],
        timestamp=t_now - 1.0,
        metadata={"session_id": "session_1", "desc": "duplicate"}
    )
    
    # Session 2 - Item 3 (Older, slightly different vector, different session)
    db.insert(
        namespace="patterns",
        key="Byzantine consensus agreement log",
        vector=[0.0, 1.0, 0.0],
        timestamp=t_now - 5000.0,  # Older -> Recency boost decay
        metadata={"session_id": "session_2", "desc": "byzantine"}
    )
    
    # Session 3 - Item 4 (New, unique topic, different session)
    db.insert(
        namespace="patterns",
        key="Gossip anti entropy state protocol",
        vector=[0.0, 0.0, 1.0],
        timestamp=t_now,
        metadata={"session_id": "session_3", "desc": "gossip"}
    )

    pipeline = SmartRetrievalPipeline(db)
    
    # Query vector matching the first topic [1.0, 0.0, 0.0]
    results = pipeline.retrieve(
        query_vector=[1.0, 0.0, 0.0],
        namespaces=["patterns"],
        limit=3,
        half_life_ms=10000.0,  # Fast decay for test
        mmr_lambda=0.5,
        current_time=t_now
    )
    
    # Expected:
    # 1. "Agent design patterns in ASG" should be first (exact match, new).
    # 2. Duplicate should be suppressed or lower because of Jaccard MMR.
    # 3. Session Round-Robin should interleave session_1, session_3, session_2.
    assert len(results) >= 2
    
    # The first result must be the exact match
    assert results[0].key == "Agent design patterns in ASG"
    
    # Verify session interleaving
    sessions = [res.metadata["session_id"] for res in results]
    # We should have elements from different sessions interleaved
    assert len(set(sessions)) >= 2


# ----------------------------------------------------------------------
# 5. MEMORY BRIDGE TESTS
# ----------------------------------------------------------------------

def test_memory_bridge_idempotency_and_wal(tmp_path):
    db = AgentDB()
    wal_file = tmp_path / "wal_log.txt"
    hash_cache = tmp_path / "hashes.json"
    
    bridge = MemoryBridge(db, wal_path=str(wal_file), hash_cache_path=str(hash_cache))
    
    # Create a source file
    src_file = tmp_path / "roster_rotation_log.md"
    src_file.write_text("Line 1: Score: 0.95 | Agent Roster succession completed.\nLine 2: Score: 0.70 | Minor feedback stored.\n")
    
    # First import: should succeed
    res1 = bridge.import_file(str(src_file), namespace="succession")
    assert res1 is True
    
    # Verify database populated
    assert len(db.namespaces["succession"].nodes) == 2
    
    # Verify WAL log
    assert os.path.exists(wal_file)
    wal_content = wal_file.read_text()
    assert "INSERT" in wal_content
    assert "Line 1" in wal_content
    
    # Second import: should be skipped (idempotency check via SHA-256)
    res2 = bridge.import_file(str(src_file), namespace="succession")
    assert res2 is False


def test_memory_bridge_context_eviction(tmp_path):
    db = AgentDB()
    wal_file = tmp_path / "wal_log.txt"
    hash_cache = tmp_path / "hashes.json"
    
    bridge = MemoryBridge(db, wal_path=str(wal_file), hash_cache_path=str(hash_cache))
    
    context_file = tmp_path / "context.txt"
    
    # Write a file with 200 lines, some scored high, some low
    lines = []
    for i in range(200):
        # We assign higher scores to lines near the end
        score = 0.1 + (i / 200.0) * 0.8
        lines.append(f"Line {i} with score={score:.2f}\n")
        
    context_file.write_text("".join(lines))
    
    # Prune to 180 lines
    bridge.prune_file(str(context_file), max_lines=180)
    
    # Verify line count
    with open(context_file, "r") as f:
        pruned_lines = f.readlines()
        
    assert len(pruned_lines) == 180
    
    # Verify that higher-scored lines are preserved
    # Since index 0 had score 0.1, it should be evicted
    # And high index lines (score close to 0.9) should be kept
    # Check if Line 0 is gone
    assert not any("Line 0 with score=" in line for line in pruned_lines)
    assert any("Line 199 with score=" in line for line in pruned_lines)


def test_memory_bridge_add_and_prune(tmp_path):
    db = AgentDB()
    wal_file = tmp_path / "wal_log.txt"
    hash_cache = tmp_path / "hashes.json"
    
    bridge = MemoryBridge(db, wal_path=str(wal_file), hash_cache_path=str(hash_cache))
    context_file = tmp_path / "context.txt"
    
    # Initial write of 170 lines
    lines = [f"Initial line {i} score=0.50\n" for i in range(170)]
    context_file.write_text("".join(lines))
    
    # Add 20 new lines and prune (total 190 -> pruned to 180)
    new_lines = [f"New line {i} score=0.99" for i in range(20)]
    bridge.add_and_prune(str(context_file), new_lines, max_lines=180)
    
    with open(context_file, "r") as f:
        final_lines = f.readlines()
        
    assert len(final_lines) == 180
    # High score new lines should be present
    assert any("New line 0 score=0.99" in line for line in final_lines)
    
    # Verify WAL logging for prune append
    wal_content = wal_file.read_text()
    assert "PRUNE_APPEND" in wal_content
