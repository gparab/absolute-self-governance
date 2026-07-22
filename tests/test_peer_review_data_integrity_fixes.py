"""Regression tests for the data-integrity fixes from the July 2026
peer-review batch: learning-state truncation race, multi-tenant
session-restore data wipe, non-atomic COW merges, HNSW tombstone
resurrection, and correlation ID loss."""

import json
import os

from unittest.mock import MagicMock

import pytest

from self_governance.db import (
    Base, engine as db_engine, SessionLocal, SovereignMemory, COWMemoryBranch, Tenant,
)
from self_governance.learning import HNSWIndex, AgentDB, save_learning_state


@pytest.fixture(autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=db_engine)
    yield


# --- #12: learning-state truncation race ------------------------------------

def test_save_learning_state_writes_atomically_via_tmp_and_replace(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    monkeypatch.setattr("self_governance.learning.LEARNING_STATE_FILE", str(state_file))

    save_learning_state({"runs_completed": 5})

    assert state_file.exists()
    assert not os.path.exists(f"{state_file}.tmp")  # temp file cleaned up via replace
    with open(state_file) as f:
        assert json.load(f) == {"runs_completed": 5}


def test_save_learning_state_never_leaves_zero_byte_file(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps({"runs_completed": 1}))
    monkeypatch.setattr("self_governance.learning.LEARNING_STATE_FILE", str(state_file))

    save_learning_state({"runs_completed": 2})

    # The old content is never visible as a truncated/partial file --
    # os.replace is atomic, so a concurrent reader always sees a complete file.
    with open(state_file) as f:
        data = json.load(f)
    assert data == {"runs_completed": 2}


# --- #13: multi-tenant session-restore data wipe ----------------------------

def test_handle_session_restore_refuses_with_multiple_tenants(tmp_path):
    from self_governance.cli import handle_session_restore

    db = SessionLocal()
    try:
        db.add(Tenant(id="t1", name="Tenant One", api_key_hash="h1"))
        db.add(Tenant(id="t2", name="Tenant Two", api_key_hash="h2"))
        db.commit()
    finally:
        db.close()

    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({"wallet": {"spent": 0.0}, "pending_milestones": [], "cached_metadata": {}}))
    args = MagicMock(file=str(session_file))

    with pytest.raises(SystemExit):
        handle_session_restore(args, MagicMock())


def test_handle_session_restore_allows_single_tenant(tmp_path):
    from self_governance.cli import handle_session_restore

    db = SessionLocal()
    try:
        db.query(Tenant).delete()
        db.add(Tenant(id="t1", name="Tenant One", api_key_hash="h1"))
        db.commit()
    finally:
        db.close()

    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({"wallet": {"spent": 0.0}, "pending_milestones": [], "cached_metadata": {}}))
    args = MagicMock(file=str(session_file))

    handle_session_restore(args, MagicMock())  # should not raise


# --- #14: non-atomic COW merge ----------------------------------------------

def test_cow_merge_commits_all_keys_in_one_transaction():
    memory = SovereignMemory()
    cow = COWMemoryBranch(parent_memory=memory)
    cow.set("k1", "v1", "agent1")
    cow.set("k2", "v2", "agent1")

    assert cow.merge() is True
    assert memory.get("k1", "agent1") == "v1"
    assert memory.get("k2", "agent1") == "v2"
    assert cow.write_buffer == {}


def test_cow_merge_failure_rolls_back_and_clears_buffer():
    mock_parent = MagicMock()
    mock_parent.set.side_effect = Exception("DB write failed")
    cow = COWMemoryBranch(parent_memory=mock_parent, db=None)
    cow.set("k1", "v1", "agent1")

    assert cow.merge() is False
    assert cow.write_buffer == {}  # no longer left dangling on failure
    assert cow.fallback_storage[("agent1", "k1")] == "v1"


# --- #15: HNSW tombstone resurrection ----------------------------------------

def test_hnsw_reinsert_after_delete_is_visible_again():
    index = HNSWIndex()
    index.insert(1, [1.0, 0.0, 0.0])
    index.delete(1)
    assert 1 in index.deleted

    index.insert(1, [1.0, 0.0, 0.0])
    assert 1 not in index.deleted

    results = index.search([1.0, 0.0, 0.0], k=5)
    ids = [node_id for _, node_id in results]
    assert 1 in ids


def test_agent_db_delete_then_reinsert_same_key_is_searchable():
    db = AgentDB()
    db.insert("patterns", "mykey", [1.0, 0.0, 0.0])
    db.delete("patterns", "mykey")
    db.insert("patterns", "mykey", [1.0, 0.0, 0.0])

    results = db.namespaces["patterns"].search([1.0, 0.0, 0.0], k=5)
    ids = [node_id for _, node_id in results]
    reinserted_id = db.key_to_id["patterns"]["mykey"]
    assert reinserted_id in ids


# --- #16: correlation ID loss -----------------------------------------------

def test_webhook_middleware_preserves_client_correlation_id():
    from self_governance.github_app import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    res = client.get("/health", headers={"X-Correlation-ID": "client-supplied-id-123"})
    assert res.headers["X-Correlation-ID"] == "client-supplied-id-123"
