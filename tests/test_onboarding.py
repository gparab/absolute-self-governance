import json
from unittest.mock import patch, MagicMock
import pytest
from self_governance.db import Base, Tenant, engine, SessionLocal
from self_governance.auth import verify_key
from self_governance.onboarding import (
    generate_webhook_secret,
    provision_tenant,
    register_github_webhook,
    run_onboarding,
)


@pytest.fixture(autouse=True)
def setup_test_db():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    db.query(Tenant).delete()
    db.commit()
    db.close()


def test_generate_webhook_secret_is_long_and_random():
    a = generate_webhook_secret()
    b = generate_webhook_secret()
    assert len(a) >= 32
    assert a != b


def test_provision_tenant_creates_row_with_hashed_key():
    result = provision_tenant("acme-corp")

    assert result["tenant_id"].startswith("t")
    assert result["api_key"].startswith(f"tenant_{result['tenant_id']}_")

    db = SessionLocal()
    row = db.query(Tenant).filter_by(id=result["tenant_id"]).first()
    db.close()
    assert row is not None
    assert row.name == "acme-corp"
    # The plaintext key is never stored -- only its hash, and it must verify.
    assert row.api_key_hash != result["api_key"]
    assert verify_key(result["api_key"], row.api_key_hash)


def test_register_github_webhook_success(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return json.dumps({"id": 12345}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = json.loads(req.data.decode())
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    hook = register_github_webhook(
        "acme", "widgets", "gh-token-123", "https://example.com/webhook", "sekrit"
    )

    assert hook["id"] == 12345
    assert captured["url"] == "https://api.github.com/repos/acme/widgets/hooks"
    assert captured["headers"]["Authorization"] == "Bearer gh-token-123"
    assert captured["body"]["config"]["url"] == "https://example.com/webhook"
    assert captured["body"]["config"]["secret"] == "sekrit"
    assert captured["body"]["events"] == ["issues", "pull_request"]


def test_register_github_webhook_raises_on_http_error(monkeypatch):
    import urllib.error

    def fake_urlopen(req, timeout=30):
        raise urllib.error.HTTPError(
            req.full_url, 422, "Unprocessable", {}, MagicMock(read=lambda: b'{"message":"Hook already exists"}')
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="Hook already exists"):
        register_github_webhook("acme", "widgets", "tok", "https://x/webhook", "s")


def test_run_onboarding_without_repo_returns_manual_steps():
    result = run_onboarding(tenant_name="solo-dev")

    assert result["webhook_auto_registered"] is False
    assert "webhook_secret" in result
    assert "api_key" in result
    assert "webhook_registration_error" not in result


def test_run_onboarding_auto_registers_when_fully_configured(monkeypatch):
    with patch("self_governance.onboarding.register_github_webhook") as mock_reg:
        mock_reg.return_value = {"id": 999}
        result = run_onboarding(
            tenant_name="acme",
            repo="acme/widgets",
            github_token="tok",
            base_url="https://tunnel.example.com",
        )

    assert result["webhook_auto_registered"] is True
    assert result["webhook_id"] == 999
    assert result["webhook_url"] == "https://tunnel.example.com/webhook"
    mock_reg.assert_called_once()
    call_args = mock_reg.call_args[0]
    assert call_args[0] == "acme"
    assert call_args[1] == "widgets"


def test_run_onboarding_falls_back_to_manual_on_registration_failure():
    with patch("self_governance.onboarding.register_github_webhook") as mock_reg:
        mock_reg.side_effect = RuntimeError("GitHub API error 401: bad token")
        result = run_onboarding(
            tenant_name="acme",
            repo="acme/widgets",
            github_token="bad-tok",
            base_url="https://tunnel.example.com",
        )

    assert result["webhook_auto_registered"] is False
    assert "bad token" in result["webhook_registration_error"]


def test_cli_demo_runs(capsys):
    import sys as _sys
    from unittest.mock import patch as _patch
    from self_governance.cli import main

    test_args = ["self-governance", "demo", "--pause", "0"]
    with _patch.object(_sys, "argv", test_args):
        main()

    out = capsys.readouterr().out
    assert "ASG demo" in out


def test_cli_onboard_manual_path(capsys):
    import sys as _sys
    from unittest.mock import patch as _patch
    from self_governance.cli import main

    test_args = ["self-governance", "onboard", "--name", "cli-test-tenant"]
    with _patch.object(_sys, "argv", test_args):
        main()

    out = capsys.readouterr().out
    assert "ASG onboarding" in out
    assert "Next steps (manual webhook setup)" in out
