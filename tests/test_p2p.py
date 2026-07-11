"""Tests for peer-agent session sharing (p2p.py)."""
import time
import pytest
from self_governance.p2p import (
    create_share_token,
    get_shared_session,
    peek_shared_session,
    revoke_share_token,
    list_active_tokens,
    purge_expired_tokens,
    _SESSION_STORE,
)


@pytest.fixture(autouse=True)
def clear_store():
    """Clear the in-memory session store between tests."""
    _SESSION_STORE.clear()
    yield
    _SESSION_STORE.clear()


class TestCreateShareToken:
    def test_creates_valid_token(self):
        token = create_share_token({"roster": ["Backend Wizard"]}, ttl_seconds=60)
        assert len(token.token) > 20
        assert token.expires_at > time.time()
        assert len(token.fingerprint) == 64  # SHA-256 hex
        assert not token.is_expired()

    def test_token_stored_in_session_store(self):
        token = create_share_token({"phase": "build"}, ttl_seconds=60)
        assert token.token in _SESSION_STORE

    def test_payload_too_large_raises(self):
        large_data = {"data": "x" * 70_000}
        with pytest.raises(ValueError, match="too large"):
            create_share_token(large_data)

    def test_created_by_stored(self):
        token = create_share_token({}, ttl_seconds=60, created_by="agent-A")
        assert token.created_by == "agent-A"

    def test_different_tokens_are_unique(self):
        t1 = create_share_token({"a": 1}, ttl_seconds=60)
        t2 = create_share_token({"a": 1}, ttl_seconds=60)
        assert t1.token != t2.token

    def test_fingerprint_is_deterministic(self):
        t1 = create_share_token({"x": 42}, ttl_seconds=60)
        t2 = create_share_token({"x": 42}, ttl_seconds=60)
        assert t1.fingerprint == t2.fingerprint  # same payload = same fingerprint


class TestGetSharedSession:
    def test_consumes_token_on_retrieve(self):
        token = create_share_token({"roster": ["QA Specialist"]}, ttl_seconds=60)
        session = get_shared_session(token.token)
        assert session == {"roster": ["QA Specialist"]}
        assert token.token not in _SESSION_STORE  # consumed

    def test_second_retrieve_returns_none(self):
        token = create_share_token({"x": 1}, ttl_seconds=60)
        get_shared_session(token.token)  # first — consumes
        result = get_shared_session(token.token)  # second — already gone
        assert result is None

    def test_expired_token_returns_none(self):
        token = create_share_token({"x": 1}, ttl_seconds=1)
        # Manually expire it
        _SESSION_STORE[token.token]["expires_at"] = time.time() - 1
        result = get_shared_session(token.token)
        assert result is None

    def test_unknown_token_returns_none(self):
        assert get_shared_session("nonexistent-token-xyz") is None


class TestPeekSharedSession:
    def test_peek_does_not_consume(self):
        token = create_share_token({"y": 2}, ttl_seconds=60)
        meta = peek_shared_session(token.token)
        assert meta is not None
        assert token.token in _SESSION_STORE  # still there

    def test_peek_returns_metadata(self):
        token = create_share_token({"z": 3}, ttl_seconds=60, created_by="agent-B")
        meta = peek_shared_session(token.token)
        assert meta.created_by == "agent-B"
        assert meta.fingerprint == token.fingerprint

    def test_peek_expired_returns_none(self):
        token = create_share_token({"z": 3}, ttl_seconds=60)
        _SESSION_STORE[token.token]["expires_at"] = time.time() - 1
        assert peek_shared_session(token.token) is None


class TestRevokeShareToken:
    def test_revoke_removes_token(self):
        token = create_share_token({}, ttl_seconds=60)
        revoked = revoke_share_token(token.token)
        assert revoked is True
        assert token.token not in _SESSION_STORE

    def test_revoke_nonexistent_returns_false(self):
        assert revoke_share_token("ghost-token") is False

    def test_revoked_token_not_retrievable(self):
        token = create_share_token({"data": "secret"}, ttl_seconds=60)
        revoke_share_token(token.token)
        assert get_shared_session(token.token) is None


class TestListAndPurge:
    def test_list_returns_active_tokens(self):
        t1 = create_share_token({"a": 1}, ttl_seconds=60)
        t2 = create_share_token({"b": 2}, ttl_seconds=60)
        active = list_active_tokens()
        tokens_listed = [t["token"] for t in active]
        assert t1.token in tokens_listed
        assert t2.token in tokens_listed

    def test_list_excludes_expired(self):
        token = create_share_token({"x": 1}, ttl_seconds=60)
        _SESSION_STORE[token.token]["expires_at"] = time.time() - 1
        active = list_active_tokens()
        assert token.token not in [t["token"] for t in active]

    def test_purge_removes_expired(self):
        t1 = create_share_token({"a": 1}, ttl_seconds=60)
        t2 = create_share_token({"b": 2}, ttl_seconds=60)
        _SESSION_STORE[t2.token]["expires_at"] = time.time() - 1
        count = purge_expired_tokens()
        assert count == 1
        assert t1.token in _SESSION_STORE
        assert t2.token not in _SESSION_STORE

    def test_purge_no_expired_returns_zero(self):
        create_share_token({}, ttl_seconds=60)
        assert purge_expired_tokens() == 0


class TestShareTokenToDict:
    def test_to_dict_has_required_keys(self):
        token = create_share_token({"x": 1}, ttl_seconds=60, created_by="agent-X")
        d = token.to_dict()
        assert "token" in d
        assert "expires_at" in d
        assert "fingerprint" in d
        assert "created_by" in d
        assert "ttl_remaining" in d
        assert d["ttl_remaining"] > 0

    def test_is_expired_false_for_new_token(self):
        token = create_share_token({}, ttl_seconds=60)
        assert not token.is_expired()
