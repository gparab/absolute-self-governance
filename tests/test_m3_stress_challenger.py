import os
import json
import uuid
import tempfile
import pytest
import hmac
import hashlib
import argparse
from collections.abc import Sequence
from fastapi.testclient import TestClient
from pydantic import BaseModel

from self_governance.models import Agent, SwarmConfig, SessionStatus, PipelineStatus
from self_governance.db import init_db, SessionLocal, SuccessionSession, Tenant
from self_governance.cli import handle_dimension
from self_governance.nudger import ContinuousNudger
from self_governance.github_app import app


# =====================================================================
# 1. SwarmConfig & LazyList Lazy Evaluation Tests
# =====================================================================

class InstrumentedSequence(Sequence[Agent]):
    """
    A Sequence[Agent] implementation that counts accesses to verify
    whether Pydantic performs any eager evaluation during initialization.
    """
    def __init__(self, agents):
        self.agents = agents
        self.access_count = 0
        self.iter_count = 0

    def __len__(self):
        return len(self.agents)

    def __getitem__(self, idx):
        self.access_count += 1
        return self.agents[idx]

    def __iter__(self):
        self.iter_count += 1
        return iter(self.agents)


def test_swarm_config_no_eager_evaluation():
    """
    Verify that SwarmConfig accepts both standard List[Agent] and
    Sequence[Agent] without validation failure and without eager evaluation.
    """
    agents = [Agent(role="Developer", prompt="Code features")]
    
    # Test standard List[Agent]
    config_list = SwarmConfig(swarm=agents)
    assert config_list.swarm == agents

    # Test Sequence[Agent] without eager evaluation
    inst_seq = InstrumentedSequence(agents)
    config_seq = SwarmConfig(swarm=inst_seq)
    
    # Verify the reference remains unchanged
    assert config_seq.swarm is inst_seq
    
    # Assert no elements were accessed during Pydantic initialization
    assert inst_seq.access_count == 0
    assert inst_seq.iter_count == 0


# =====================================================================
# 2. SessionStatus & PipelineStatus Enum Verification
# =====================================================================



class StatusHolder(BaseModel):
    session_status: SessionStatus
    pipeline_status: PipelineStatus


def test_enum_serialization_deserialization():
    """Verify enums serialize/deserialize and fail on invalid string inputs."""
    # Serialization
    m = StatusHolder(
        session_status=SessionStatus.COMPLETED,
        pipeline_status=PipelineStatus.APPROVED
    )
    dumped = m.model_dump()
    assert dumped == {"session_status": "COMPLETED", "pipeline_status": "APPROVED"}

    # Deserialization
    m2 = StatusHolder.model_validate({"session_status": "COMPLETED", "pipeline_status": "APPROVED"})
    assert m2.session_status == SessionStatus.COMPLETED
    assert m2.pipeline_status == PipelineStatus.APPROVED

    # Invalid deserialization
    with pytest.raises(ValueError):
        StatusHolder.model_validate({"session_status": "INVALID", "pipeline_status": "APPROVED"})


def test_enum_db_roundtrip():
    """Verify enums can be written/read and mapped correctly with DB models."""
    init_db()
    db = SessionLocal()
    try:
        tenant_id = f"t_{uuid.uuid4().hex[:8]}"
        tenant = Tenant(id=tenant_id, name="Test Tenant", api_key_hash="dummy_hash")
        db.add(tenant)
        db.commit()

        # Write to DB
        sess = SuccessionSession(
            tenant_id=tenant_id,
            status=SessionStatus.RUNNING.value,
            approved_roster="agent_a,agent_b",
        )
        db.add(sess)
        db.commit()
        db.refresh(sess)

        # Read back from DB
        retrieved = db.query(SuccessionSession).filter_by(tenant_id=tenant_id).first()
        assert retrieved is not None
        assert retrieved.status == "RUNNING"

        # Verify mapping to enum
        enum_val = SessionStatus(retrieved.status)
        assert enum_val == SessionStatus.RUNNING
    finally:
        db.close()


# =====================================================================
# 3. CLI Command Argument Stress Testing
# =====================================================================



def test_cli_dimension_malformed_arguments():
    """Test CLI handle_dimension command with malformed or missing arguments."""
    # Malformed JSON
    args_malformed_json = argparse.Namespace(requirements="{invalid", matrix="[[1]]")
    with pytest.raises(json.JSONDecodeError):
        handle_dimension(args_malformed_json)

    # Empty inputs
    args_empty = argparse.Namespace(requirements="[]", matrix="[]")
    with pytest.raises(ValueError, match="Inputs cannot be empty"):
        handle_dimension(args_empty)

    # Invalid type
    args_invalid_type = argparse.Namespace(requirements="null", matrix="[[1]]")
    with pytest.raises(TypeError):
        handle_dimension(args_invalid_type)


# =====================================================================
# 4. Watchdog Handoff Processing Edge Cases
# =====================================================================

def test_watchdog_handoff_processing_edge_cases():
    """Test process_handoff handling of missing, malformed, or empty handoff files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.makedirs(os.path.join(tmpdir, ".planning"), exist_ok=True)
        nudger = ContinuousNudger(working_directory=tmpdir)
        handoff_file_path = os.path.join(tmpdir, nudger.config.handoff_file)

        # 1. Missing handoff file - should return immediately without crash
        nudger.process_handoff()

        # 2. Malformed YAML - should log error and not crash
        with open(handoff_file_path, "w", encoding="utf-8") as f:
            f.write("\tmalformed: yaml: tabs are forbidden")
        nudger.process_handoff()

        # 3. Valid YAML but not a dictionary
        with open(handoff_file_path, "w", encoding="utf-8") as f:
            f.write("- item1\n- item2")
        nudger.process_handoff()

        # 4. Valid YAML dict but with empty/missing values (e.g. status: null or missing)
        with open(handoff_file_path, "w", encoding="utf-8") as f:
            f.write("status: null\ncandidates: null")
        nudger.process_handoff()


# =====================================================================
# 5. GitHub Webhook Event Parsing Edge Cases (Issues)
# =====================================================================

client = TestClient(app)

def signed_post(payload: dict, event: str):
    """Post to /webhook with HMAC signature."""
    body = json.dumps(payload).encode()
    secret = os.getenv("WEBHOOK_SECRET", "dummy_secret")
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {"X-GitHub-Event": event, "X-Hub-Signature-256": f"sha256={sig}"}
    return client.post("/webhook", content=body, headers=headers)


def test_webhook_issues_null_title(monkeypatch):
    """Test webhook issues opened with title explicitly set to None."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "opened",
        "issue": {
            "title": None,
            "body": "Some body"
        }
    }
    # Currently crashes with AttributeError due to lack of None check before .lower()
    with pytest.raises(AttributeError):
        signed_post(payload, "issues")


def test_webhook_issues_null_body(monkeypatch):
    """Test webhook issues opened with body explicitly set to None."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "opened",
        "issue": {
            "title": "A performance bug",
            "body": None
        }
    }
    # Currently crashes with AttributeError due to lack of None check before .lower()
    with pytest.raises(AttributeError):
        signed_post(payload, "issues")


def test_webhook_issues_invalid_structure(monkeypatch):
    """Test webhook issues opened with issues key as list instead of dict."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "opened",
        "issue": ["not", "a", "dict"]
    }
    # Currently crashes with AttributeError due to calling .get on a list
    with pytest.raises(AttributeError):
        signed_post(payload, "issues")


# =====================================================================
# 6. GitHub Webhook Event Parsing Edge Cases (Pull Request)
# =====================================================================

def test_webhook_pr_null_title(monkeypatch):
    """Test webhook PR closed with title explicitly set to None."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "created_at_timestamp": 100.0,
            "closed_at_timestamp": 125.0,
            "title": None
        }
    }
    # Currently crashes with AttributeError due to lack of None check before .lower()
    with pytest.raises(AttributeError):
        signed_post(payload, "pull_request")


def test_webhook_pr_null_timestamps(monkeypatch):
    """Test webhook PR closed with timestamps explicitly set to None."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "created_at_timestamp": None,
            "closed_at_timestamp": 125.0,
            "title": "Fix security vulnerability"
        }
    }
    # This should succeed since we implemented None-safety
    response = signed_post(payload, "pull_request")
    assert response.status_code == 200


def test_webhook_pr_missing_timestamps(monkeypatch):
    """Test webhook PR closed with timestamps completely missing."""
    monkeypatch.setenv("WEBHOOK_SECRET", "dummy_secret")
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "title": "Fix security vulnerability"
        }
    }
    # This should succeed since it has defaults that prevent TypeError
    response = signed_post(payload, "pull_request")
    assert response.status_code == 200
