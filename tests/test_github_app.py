import pytest
from fastapi.testclient import TestClient
from self_governance.github_app import app
from self_governance.learning import LEARNING_STATE_FILE
import os

client = TestClient(app)

@pytest.fixture(autouse=True)
def clean_learning_state():
    if os.path.exists(LEARNING_STATE_FILE):
        os.remove(LEARNING_STATE_FILE)
    yield
    if os.path.exists(LEARNING_STATE_FILE):
        os.remove(LEARNING_STATE_FILE)

def test_webhook_ping():
    response = client.post("/webhook", json={}, headers={"X-GitHub-Event": "ping"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "msg": "pong"}

def test_webhook_issue_opened(tmp_path):
    # Mock working directory where handoff is written
    payload = {
        "action": "opened",
        "issue": {
            "title": "Performance regression in dynamic dimensioning",
            "body": "The LazyList lookup is slow"
        }
    }
    
    # We create a dummy handoff file so the trigger succeeds in the webhook folder
    os.makedirs(tmp_path, exist_ok=True)
    
    # Patch working directory to prevent writing to real root during test
    from unittest.mock import patch
    with patch("self_governance.github_app.nudger.working_directory", str(tmp_path)):
        # Write candidates file in the mocked directory
        handoff_path = os.path.join(str(tmp_path), "handoff.md")
        with open(handoff_path, "w") as f:
            f.write("status: COMPLETED\ncandidates:\n  - agent_1\n")
            
        response = client.post("/webhook", json=payload, headers={"X-GitHub-Event": "issues"})
        assert response.status_code == 200
        json_data = response.json()
        assert json_data["status"] == "success"
        assert json_data["requirements"][0] == 5.0  # Heuristic triggered 'performance'

def test_webhook_pr_closed_merged():
    payload = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "created_at_timestamp": 100.0,
            "closed_at_timestamp": 125.0,
            "title": "Fix security vulnerability CVE-1234"
        }
    }
    
    response = client.post("/webhook", json=payload, headers={"X-GitHub-Event": "pull_request"})
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    
    # Verify learning state holds security alert counts
    from self_governance.learning import get_learning_state
    state = get_learning_state()
    assert state["vulnerability_counts"] == 1
    assert state["average_cycle_time"] == 25.0

def test_webhook_hmac_verification(monkeypatch):
    import hmac
    import hashlib
    
    # Configure webhook secret environment
    monkeypatch.setenv("WEBHOOK_SECRET", "super_secret_token")
    
    # Send request without signature
    response = client.post("/webhook", json={}, headers={"X-GitHub-Event": "ping"})
    assert response.status_code == 401
    assert response.json()["detail"] == "Missing X-Hub-Signature-256 header"

    # Send request with invalid signature
    response = client.post("/webhook", json={}, headers={
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": "sha256=invalid_hash"
    })
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid signature"

    # Send request with valid signature
    body_data = b"{}"
    expected_hash = hmac.new(b"super_secret_token", body_data, hashlib.sha256).hexdigest()
    response = client.post("/webhook", content=body_data, headers={
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": f"sha256={expected_hash}"
    })
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "msg": "pong"}

def test_webhook_mandatory_secret(monkeypatch):
    import sys
    monkeypatch.delenv("WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("TESTING", "False")
    
    # Remove from sys.modules if already imported
    if "self_governance.github_app" in sys.modules:
        del sys.modules["self_governance.github_app"]
        
    with pytest.raises(ValueError, match="WEBHOOK_SECRET environment variable is required"):
        pass


