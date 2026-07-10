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
