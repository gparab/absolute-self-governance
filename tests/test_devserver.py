from fastapi.testclient import TestClient
from self_governance.devserver import dev_app

client = TestClient(dev_app)


def test_health():
    assert client.get("/health").json() == {"status": "ok"}


def test_status_shape():
    data = client.get("/status").json()
    for key in (
        "runs_completed",
        "success_rate",
        "session_cost_usd",
        "consensus_iterations",
    ):
        assert key in data


def test_index_serves_page():
    res = client.get("/")
    assert res.status_code == 200
    assert "ASG Dev Monitor" in res.text


def test_p2p_share_api_flow():
    # 1. Share a session
    payload = {
        "session_data": {"roster": ["Backend Wizard"], "phase": "build"},
        "ttl_seconds": 60,
        "created_by": "agent-A"
    }
    share_res = client.post("/api/p2p/share", json=payload)
    assert share_res.status_code == 200
    data = share_res.json()
    assert "token" in data
    assert "expires_at" in data
    assert "fingerprint" in data
    assert data["created_by"] == "agent-A"
    token = data["token"]

    # 2. List active tokens
    list_res = client.get("/api/p2p/tokens")
    assert list_res.status_code == 200
    tokens = [t["token"] for t in list_res.json()["tokens"]]
    assert token in tokens

    # 3. Peek at token (should not consume)
    peek_res = client.get(f"/api/p2p/session/{token}?peek=true")
    assert peek_res.status_code == 200
    assert peek_res.json()["created_by"] == "agent-A"

    # 4. Consume token
    get_res = client.get(f"/api/p2p/session/{token}")
    assert get_res.status_code == 200
    assert get_res.json()["session_data"] == {"roster": ["Backend Wizard"], "phase": "build"}

    # 5. Retrieve again (should be 404 since consumed)
    second_res = client.get(f"/api/p2p/session/{token}")
    assert second_res.status_code == 404


def test_p2p_revoke_flow():
    # 1. Share
    payload = {"session_data": {"roster": ["QA Specialist"]}}
    share_res = client.post("/api/p2p/share", json=payload)
    token = share_res.json()["token"]

    # 2. Revoke
    revoke_res = client.delete(f"/api/p2p/session/{token}")
    assert revoke_res.status_code == 200
    assert revoke_res.json()["revoked"] is True

    # 3. Try to consume (should fail)
    get_res = client.get(f"/api/p2p/session/{token}")
    assert get_res.status_code == 404
