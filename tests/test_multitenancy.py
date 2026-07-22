import pytest
from fastapi.testclient import TestClient
from self_governance.db import (
    Base,
    Tenant,
    SuccessionSession,
    TokenUsage,
    engine,
    SessionLocal,
)
from self_governance.billing import record_usage
from self_governance.models import SessionStatus
from self_governance.github_app import app


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    # Seed test tenants
    db = SessionLocal()

    # Clean up existing rows
    db.query(TokenUsage).delete()
    db.query(SuccessionSession).delete()
    db.query(Tenant).delete()
    db.commit()

    from self_governance.auth import hash_key

    tenant_a = Tenant(
        id="tenantA",
        name="Tenant Alpha",
        api_key_hash=hash_key("tenant_tenantA_key"),
    )
    tenant_b = Tenant(
        id="tenantB",
        name="Tenant Beta",
        api_key_hash=hash_key("tenant_tenantB_key"),
    )
    db.add(tenant_a)
    db.add(tenant_b)
    db.commit()
    db.close()

    yield


def test_db_tenant_isolation():
    db = SessionLocal()

    # Save session for tenantA
    sess_a = SuccessionSession(
        tenant_id="tenantA", status=SessionStatus.COMPLETED.value, approved_roster="agent_dev"
    )
    db.add(sess_a)
    db.commit()

    # Save session for tenantB
    sess_b = SuccessionSession(
        tenant_id="tenantB", status=SessionStatus.PENDING.value, approved_roster="agent_tester"
    )
    db.add(sess_b)
    db.commit()

    # Query separately
    a_sessions = (
        db.query(SuccessionSession)
        .filter(SuccessionSession.tenant_id == "tenantA")
        .all()
    )
    b_sessions = (
        db.query(SuccessionSession)
        .filter(SuccessionSession.tenant_id == "tenantB")
        .all()
    )

    assert len(a_sessions) == 1
    assert a_sessions[0].status == SessionStatus.COMPLETED.value

    assert len(b_sessions) == 1
    assert b_sessions[0].status == SessionStatus.PENDING.value
    db.close()


def test_billing_record_usage():
    db = SessionLocal()
    usage = record_usage(
        tenant_id="tenantA",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=0.000225,
        db=db,
    )
    assert usage.tenant_id == "tenantA"
    assert usage.prompt_tokens == 1000
    assert usage.cost_usd == 0.000225
    db.close()


def test_status_renders_tenant_data():
    client = TestClient(app)

    # Verify Tenant A's status
    response_a = client.get(
        "/status", headers={"Authorization": "Bearer tenant_tenantA_key"}
    )
    assert response_a.status_code == 200
    data_a = response_a.json()
    assert data_a["tenant_id"] == "tenantA"

    # Verify Tenant B's status
    response_b = client.get(
        "/status", headers={"Authorization": "Bearer tenant_tenantB_key"}
    )
    assert response_b.status_code == 200
    data_b = response_b.json()
    assert data_b["tenant_id"] == "tenantB"


def test_webhook_adds_db_records(monkeypatch):
    async def mock_verify(req):
        return None

    monkeypatch.setattr("self_governance.github_app.verify_signature", mock_verify)

    client = TestClient(app)

    # Post webhook using Tenant A auth token
    payload = {
        "action": "opened",
        "issue": {
            "title": "Database connection optimization",
            "body": "Optimize performance of tenant queries",
        },
    }

    response = client.post(
        "/webhook",
        json=payload,
        headers={
            "X-GitHub-Event": "issues",
            "Authorization": "Bearer tenant_tenantA_key",
        },
    )
    assert response.status_code == 200

    # Query DB to make sure a session was created for Tenant A
    db = SessionLocal()
    sessions = (
        db.query(SuccessionSession)
        .filter(SuccessionSession.tenant_id == "tenantA")
        .all()
    )
    usages = db.query(TokenUsage).filter(TokenUsage.tenant_id == "tenantA").all()

    assert len(sessions) == 1
    # webhook_matrix's 4th row used to fall through to a "role_3" dummy
    # placeholder persona (peer-review batch, July 2026: LazyList's
    # role_map only covered 3 of the default 4 matrix rows) -- it now
    # resolves to a real registered persona instead.
    assert "DevOps Automator" in sessions[0].approved_roster
    assert len(usages) == 1
    assert usages[0].prompt_tokens == 500
    db.close()


def test_create_tenant_endpoint():
    client = TestClient(app)
    response = client.post("/tenants", json={"name": "New Swarm Tenant"})
    assert response.status_code == 200
    data = response.json()
    assert "tenant_id" in data
    assert "api_key" in data

    tenant_id = data["tenant_id"]
    api_key = data["api_key"]

    response_dash = client.get(
        "/status", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert response_dash.status_code == 200
    assert response_dash.json()["tenant_id"] == tenant_id


def test_rate_limiting_enforcement():
    from self_governance.db import SessionLocal, RateLimitEntry

    db = SessionLocal()
    db.query(RateLimitEntry).filter(RateLimitEntry.tenant_id == "tenantA").delete()

    import time

    now = time.time()
    for _ in range(100):
        entry = RateLimitEntry(tenant_id="tenantA", timestamp=now)
        db.add(entry)
    db.commit()
    db.close()

    client = TestClient(app)
    response = client.get(
        "/status", headers={"Authorization": "Bearer tenant_tenantA_key"}
    )
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["detail"]

    db = SessionLocal()
    db.query(RateLimitEntry).filter(RateLimitEntry.tenant_id == "tenantA").delete()
    db.commit()
    db.close()


def test_trigger_succession_isolates_tenant_output_files(tmp_path):
    """Two tenants calling trigger_succession concurrently must not clobber
    or leak each other's prompt_draft.md / roster_rotation_log.md."""
    from self_governance.nudger import ContinuousNudger

    nudger = ContinuousNudger(working_directory=str(tmp_path))

    nudger.trigger_succession(
        "status: COMPLETED\ncandidates: [agent_alpha_dev]", tenant_id="tenantA"
    )
    nudger.trigger_succession(
        "status: COMPLETED\ncandidates: [agent_beta_dev]", tenant_id="tenantB"
    )

    prompt_a = tmp_path / "tenants" / "tenantA" / nudger.config.prompt_file
    prompt_b = tmp_path / "tenants" / "tenantB" / nudger.config.prompt_file
    log_a = tmp_path / "tenants" / "tenantA" / nudger.config.roster_log_file
    log_b = tmp_path / "tenants" / "tenantB" / nudger.config.roster_log_file

    assert prompt_a.exists() and prompt_b.exists()
    assert log_a.exists() and log_b.exists()
    assert prompt_a != prompt_b

    assert "agent_alpha_dev" in log_a.read_text()
    assert "agent_beta_dev" not in log_a.read_text()
    assert "agent_beta_dev" in log_b.read_text()
    assert "agent_alpha_dev" not in log_b.read_text()

    # No plain files leaked at the shared working_directory root.
    assert not (tmp_path / nudger.config.prompt_file).exists()
    assert not (tmp_path / nudger.config.roster_log_file).exists()
