import os
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from self_governance.db import Base, Tenant, SuccessionSession, TokenUsage, get_db, engine, SessionLocal
from self_governance.auth import authenticate_tenant
from self_governance.billing import record_usage
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
    
    tenant_a = Tenant(id="tenantA", name="Tenant Alpha", stripe_customer_id="cus_alpha123", api_key_hash="hashA")
    tenant_b = Tenant(id="tenantB", name="Tenant Beta", stripe_customer_id="cus_beta456", api_key_hash="hashB")
    db.add(tenant_a)
    db.add(tenant_b)
    db.commit()
    db.close()
    
    yield

def test_db_tenant_isolation():
    db = SessionLocal()
    
    # Save session for tenantA
    sess_a = SuccessionSession(tenant_id="tenantA", status="COMPLETED", approved_roster="agent_dev")
    db.add(sess_a)
    db.commit()
    
    # Save session for tenantB
    sess_b = SuccessionSession(tenant_id="tenantB", status="PENDING", approved_roster="agent_tester")
    db.add(sess_b)
    db.commit()
    
    # Query separately
    a_sessions = db.query(SuccessionSession).filter(SuccessionSession.tenant_id == "tenantA").all()
    b_sessions = db.query(SuccessionSession).filter(SuccessionSession.tenant_id == "tenantB").all()
    
    assert len(a_sessions) == 1
    assert a_sessions[0].status == "COMPLETED"
    
    assert len(b_sessions) == 1
    assert b_sessions[0].status == "PENDING"
    db.close()

def test_billing_record_usage():
    db = SessionLocal()
    usage = record_usage(
        tenant_id="tenantA",
        prompt_tokens=1000,
        completion_tokens=500,
        cost_usd=0.000225,
        db=db
    )
    assert usage.tenant_id == "tenantA"
    assert usage.prompt_tokens == 1000
    assert usage.cost_usd == 0.000225
    db.close()

def test_dashboard_renders_tenant_data():
    client = TestClient(app)
    
    # Verify Tenant A's dashboard
    response_a = client.get("/dashboard", headers={"Authorization": "Bearer tenant_tenantA_key"})
    assert response_a.status_code == 200
    assert "Tenant Context: tenantA" in response_a.text
    assert "cus_alpha123" in response_a.text
    
    # Verify Tenant B's dashboard
    response_b = client.get("/dashboard", headers={"Authorization": "Bearer tenant_tenantB_key"})
    assert response_b.status_code == 200
    assert "Tenant Context: tenantB" in response_b.text
    assert "cus_beta456" in response_b.text

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
            "body": "Optimize performance of tenant queries"
        }
    }
    
    response = client.post(
        "/webhook",
        json=payload,
        headers={
            "X-GitHub-Event": "issues",
            "Authorization": "Bearer tenant_tenantA_key"
        }
    )
    assert response.status_code == 200
    
    # Query DB to make sure a session was created for Tenant A
    db = SessionLocal()
    sessions = db.query(SuccessionSession).filter(SuccessionSession.tenant_id == "tenantA").all()
    usages = db.query(TokenUsage).filter(TokenUsage.tenant_id == "tenantA").all()
    
    assert len(sessions) == 1
    assert "role_" in sessions[0].approved_roster
    assert len(usages) == 1
    assert usages[0].prompt_tokens == 500
    db.close()
