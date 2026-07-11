"""Peer-agent session sharing for ASG orchestration.

Enables one agent to share its active succession context with a peer agent
via a short-lived token. The handoff is cryptographically signed and expires
after a configurable TTL.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

# In-memory session store (process-scoped)
_SESSION_STORE: Dict[str, Dict[str, Any]] = {}

DEFAULT_TTL_SECONDS = 300  # 5 minutes
MAX_SESSION_SIZE_BYTES = 65_536  # 64 KB


@dataclass
class ShareToken:
    """A short-lived token for sharing an agent session."""
    token: str
    expires_at: float
    fingerprint: str  # SHA-256 of the session payload
    created_by: str = "unknown"

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "token": self.token,
            "expires_at": self.expires_at,
            "fingerprint": self.fingerprint,
            "created_by": self.created_by,
            "ttl_remaining": max(0.0, self.expires_at - time.time()),
        }


def create_share_token(
    session_data: Dict[str, Any],
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    created_by: str = "unknown",
) -> ShareToken:
    """Create a share token for the given session data.

    Args:
        session_data: The session dict to share (e.g. approved_roster, pipeline phase).
        ttl_seconds: Seconds until the token expires. Default 300 (5 min).
        created_by: Identifier of the creating agent (tenant_id, agent role, etc.).

    Returns:
        ShareToken with a cryptographically random token string.

    Raises:
        ValueError: If session_data exceeds MAX_SESSION_SIZE_BYTES when serialized.
    """
    payload_bytes = json.dumps(session_data, default=str).encode("utf-8")
    if len(payload_bytes) > MAX_SESSION_SIZE_BYTES:
        raise ValueError(
            f"Session payload too large: {len(payload_bytes)} bytes "
            f"(max {MAX_SESSION_SIZE_BYTES})"
        )

    token_str = secrets.token_urlsafe(32)
    expires_at = time.time() + ttl_seconds
    fingerprint = hashlib.sha256(payload_bytes).hexdigest()

    _SESSION_STORE[token_str] = {
        "data": session_data,
        "expires_at": expires_at,
        "fingerprint": fingerprint,
        "created_by": created_by,
    }

    return ShareToken(
        token=token_str,
        expires_at=expires_at,
        fingerprint=fingerprint,
        created_by=created_by,
    )


def get_shared_session(token: str) -> Optional[Dict[str, Any]]:
    """Retrieve and validate a shared session by token.

    The token is consumed on first use (one-time handoff semantics).

    Args:
        token: The token string from create_share_token().

    Returns:
        The session dict if valid and not expired, else None.
    """
    entry = _SESSION_STORE.pop(token, None)
    if entry is None:
        return None
    if time.time() > entry["expires_at"]:
        return None  # Expired — already removed from store
    return entry["data"]


def peek_shared_session(token: str) -> Optional[ShareToken]:
    """Peek at a token's metadata without consuming it.

    Args:
        token: The token string.

    Returns:
        ShareToken metadata if the token exists and is not expired, else None.
    """
    entry = _SESSION_STORE.get(token)
    if entry is None:
        return None
    if time.time() > entry["expires_at"]:
        _SESSION_STORE.pop(token, None)
        return None
    return ShareToken(
        token=token,
        expires_at=entry["expires_at"],
        fingerprint=entry["fingerprint"],
        created_by=entry["created_by"],
    )


def revoke_share_token(token: str) -> bool:
    """Revoke a share token before it is consumed.

    Args:
        token: The token string to revoke.

    Returns:
        True if the token existed and was revoked, False if not found.
    """
    return _SESSION_STORE.pop(token, None) is not None


def list_active_tokens() -> list:
    """List all active (non-expired) share tokens.

    Returns:
        List of ShareToken metadata dicts (no session data exposed).
    """
    now = time.time()
    expired = [t for t, e in _SESSION_STORE.items() if now > e["expires_at"]]
    for t in expired:
        _SESSION_STORE.pop(t, None)
    return [
        ShareToken(
            token=t,
            expires_at=e["expires_at"],
            fingerprint=e["fingerprint"],
            created_by=e["created_by"],
        ).to_dict()
        for t, e in _SESSION_STORE.items()
    ]


def purge_expired_tokens() -> int:
    """Remove all expired tokens from the store.

    Returns:
        Number of tokens purged.
    """
    now = time.time()
    expired = [t for t, e in _SESSION_STORE.items() if now > e["expires_at"]]
    for t in expired:
        _SESSION_STORE.pop(t, None)
    return len(expired)


class SwarmMarket:
    """A market-based task delegation coordinator for swarming agents."""
    def __init__(self):
        self.agents = {}
        self.tasks = {}
        self.bids = {}

    def register_agent(self, agent_id: str, capabilities: list[str]) -> None:
        self.agents[agent_id] = capabilities

    def broadcast_task(self, task_id: str, description: str, required_capabilities: list[str]) -> None:
        self.tasks[task_id] = {
            "description": description,
            "required_capabilities": required_capabilities,
        }
        self.bids[task_id] = []

    def submit_bid(self, task_id: str, agent_id: str, suitability: float, cost: float) -> None:
        if task_id not in self.tasks:
            raise ValueError(f"Task {task_id} not found")
        if agent_id not in self.agents:
            raise ValueError(f"Agent {agent_id} not registered")
        self.bids[task_id].append({
            "agent_id": agent_id,
            "suitability": suitability,
            "cost": cost,
        })

    def select_winning_bid(self, task_id: str) -> Optional[dict]:
        bids = self.bids.get(task_id, [])
        if not bids:
            return None
        # Sort by suitability descending, then cost ascending
        bids_sorted = sorted(bids, key=lambda b: (-b["suitability"], b["cost"]))
        return bids_sorted[0]


class GossipProtocol:
    """A peer-to-peer knowledge gossip protocol for shared institutional memory."""
    def __init__(self):
        self.peers = {}
        self.state = {}

    def register_peer(self, peer_id: str, peer_instance: GossipProtocol) -> None:
        self.peers[peer_id] = peer_instance

    def update_local_state(self, key: str, val: Any, version: int) -> None:
        self.state[key] = (val, version)

    def gossip(self) -> int:
        updates = 0
        for peer_id, peer in self.peers.items():
            for key, (val, version) in self.state.items():
                peer_val = peer.state.get(key)
                if peer_val is None or peer_val[1] < version:
                    peer.update_local_state(key, val, version)
                    updates += 1
        return updates


class BoundedSet:
    """A set wrapper that maintains a maximum size by evicting the oldest items."""
    def __init__(self, max_size: int):
        self.max_size = max_size
        self._items: list[Any] = []
        self._set: set[Any] = set()

    def add(self, item: Any) -> None:
        if item in self._set:
            return
        if len(self._items) >= self.max_size:
            oldest = self._items.pop(0)
            self._set.remove(oldest)
        self._items.append(item)
        self._set.add(item)

    def __len__(self) -> int:
        return len(self._set)

    def __contains__(self, item: Any) -> bool:
        return item in self._set


class EnhancedGossipProtocol(GossipProtocol):
    """An enhanced gossip protocol with message IDs, TTL controls, and deduplication."""
    def __init__(self, node_id: str, max_seen_size: int = 1000, default_ttl: int = 5):
        super().__init__()
        self.node_id = node_id
        self.default_ttl = default_ttl
        self.seen_messages = BoundedSet(max_size=max_seen_size)

    def receive_gossip_enhanced(self, msg_id: str, key: str, val: Any, version: int, ttl: int) -> bool:
        if msg_id in self.seen_messages:
            return False
        if ttl <= 0:
            return False
        self.seen_messages.add(msg_id)
        self.update_local_state(key, val, version)
        # Forward to other peers
        for peer_id, peer in self.peers.items():
            if isinstance(peer, EnhancedGossipProtocol):
                peer.receive_gossip_enhanced(msg_id, key, val, version, ttl - 1)
            else:
                # Plain peer compatibility
                peer.update_local_state(key, val, version)
        return True

    def publish_gossip(self, key: str, val: Any, version: int) -> str:
        msg_id = secrets.token_hex(8)
        self.seen_messages.add(msg_id)
        self.update_local_state(key, val, version)
        for peer_id, peer in self.peers.items():
            if isinstance(peer, EnhancedGossipProtocol):
                peer.receive_gossip_enhanced(msg_id, key, val, version, self.default_ttl)
            else:
                peer.update_local_state(key, val, version)
        return msg_id

    def anti_entropy_merge(self, peer: EnhancedGossipProtocol) -> None:
        """Bidirectionally sync state between self and peer, resolving conflicts using the version."""
        all_keys = set(self.state.keys()) | set(peer.state.keys())
        for key in all_keys:
            local = self.state.get(key)
            remote = peer.state.get(key)
            if local is None:
                # Peer has it, we don't
                self.update_local_state(key, remote[0], remote[1])
            elif remote is None:
                # We have it, peer doesn't
                peer.update_local_state(key, local[0], local[1])
            else:
                # Both have it, choose newer version
                if local[1] < remote[1]:
                    self.update_local_state(key, remote[0], remote[1])
                elif remote[1] < local[1]:
                    peer.update_local_state(key, local[0], local[1])
