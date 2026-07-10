import json
import logging
from fastapi.testclient import TestClient
from self_governance.telemetry import (
    new_correlation_id,
    get_correlation_id,
    set_correlation_id,
    StructuredJSONFormatter,
)
from self_governance.github_app import app


def test_correlation_id_context():
    cid = new_correlation_id()
    assert len(cid) > 0
    assert get_correlation_id() == cid

    set_correlation_id("test-id-123")
    assert get_correlation_id() == "test-id-123"


def test_structured_json_formatter():
    formatter = StructuredJSONFormatter()
    set_correlation_id("json-test-id")

    # Create a dummy LogRecord
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test_file.py",
        lineno=10,
        msg="Structured message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)
    log_data = json.loads(formatted)

    assert log_data["level"] == "INFO"
    assert log_data["logger"] == "test_logger"
    assert log_data["message"] == "Structured message"
    assert log_data["correlation_id"] == "json-test-id"
    assert "timestamp" in log_data


def test_structured_json_formatter_includes_known_extra_fields():
    """tenant_id/event_type/duration_ms passed via extra= must survive into
    JSON output; the formatter only reads a fixed key set, not record.__dict__
    wholesale, so a newly attached field silently vanishing is a real regression."""
    formatter = StructuredJSONFormatter()
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname="test_file.py",
        lineno=10,
        msg="Succession session completed",
        args=(),
        exc_info=None,
    )
    record.tenant_id = "tenantA"
    record.event_type = "issues"
    record.duration_ms = 123.4

    log_data = json.loads(formatter.format(record))

    assert log_data["tenant_id"] == "tenantA"
    assert log_data["event_type"] == "issues"
    assert log_data["duration_ms"] == 123.4

    # A field that was never attached must not appear at all.
    formatter_bare = StructuredJSONFormatter()
    bare_record = logging.LogRecord(
        name="test_logger", level=logging.INFO, pathname="test_file.py",
        lineno=10, msg="no extras", args=(), exc_info=None,
    )
    bare_data = json.loads(formatter_bare.format(bare_record))
    assert "tenant_id" not in bare_data


def test_metrics_endpoint():
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert "asg_webhook_events_total" in response.text


def test_webhook_event_increments_metric(monkeypatch):
    # Mock signature verification
    async def mock_verify(req):
        return None

    monkeypatch.setattr("self_governance.github_app.verify_signature", mock_verify)

    client = TestClient(app)

    # Before request
    client.get("/metrics").text

    # Post issue payload
    payload = {
        "action": "opened",
        "issue": {
            "title": "Fix bug in database connection",
            "body": "Connection hangs indefinitely under load",
        },
    }

    response = client.post(
        "/webhook", json=payload, headers={"X-GitHub-Event": "issues"}
    )
    assert response.status_code == 200

    # Check metric is incremented
    after_text = client.get("/metrics").text
    assert 'asg_webhook_events_total{event_type="issues"}' in after_text
