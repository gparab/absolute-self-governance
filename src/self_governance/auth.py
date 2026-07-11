"""Authentication and authorization middleware module.

Handles tenant identification, token verification, context binding,
and rate-limiting using database backing.
"""

import contextvars
import hmac
import hashlib
import os
import time
import secrets
from fastapi import HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from self_governance.db import get_db, Tenant, RateLimitEntry

tenant_id_var = contextvars.ContextVar("tenant_id", default="")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

# Deliberately independent of TESTING: a test flag must never widen the
# production auth boundary.
ALLOW_GUEST_ACCESS = os.getenv("ALLOW_GUEST_ACCESS", "False").lower() in (
    "true",
    "1",
    "yes",
)


def get_current_tenant_id() -> str:
    """Retrieves the current tenant ID from contextvars.

    Returns:
        The active tenant identifier, or an empty string if not set.
    """
    return tenant_id_var.get()


def set_current_tenant_id(tenant_id: str) -> None:
    """Sets the active tenant ID in contextvars.

    Args:
        tenant_id: The tenant identifier to bind to the current context.
    """
    tenant_id_var.set(tenant_id)


def verify_key(key: str, hashed: str) -> bool:
    """Verifies a plaintext key against a hashed representation.

    Supports both legacy SHA-256 and PBKDF2 formats.

    Args:
        key: The plaintext key to check.
        hashed: The target hash string from storage.

    Returns:
        True if the key matches the hash, False otherwise.
    """
    if not hashed:
        return False
    if hashed.startswith("pbkdf2_sha256$"):
        try:
            parts = hashed.split("$")
            if len(parts) != 4:
                return False
            _, iterations_str, salt, target_hash = parts
            iterations = int(iterations_str)
            computed_hash = hashlib.pbkdf2_hmac(
                "sha256", key.encode("utf-8"), salt.encode("utf-8"), iterations
            ).hex()
            return hmac.compare_digest(computed_hash, target_hash)
        except Exception:
            return False
    else:
        # Fallback to legacy SHA-256
        legacy_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return hmac.compare_digest(legacy_hash, hashed)


def hash_key(key: str) -> str:
    """Generates a secure PBKDF2 hash of a plaintext key.

    Args:
        key: The plaintext key to hash.

    Returns:
        A formatted string containing PBKDF2 hashing metadata and the hex digest.
    """
    iterations = 100000
    salt = secrets.token_hex(8)
    hash_val = hashlib.pbkdf2_hmac(
        "sha256", key.encode("utf-8"), salt.encode("utf-8"), iterations
    ).hex()
    return f"pbkdf2_sha256${iterations}${salt}${hash_val}"


async def authenticate_tenant(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> Tenant:
    """Authenticates API keys (OAuth2 Bearer token) and sets tenant context.

    Args:
        token: The bearer token string.
        db: Database session for looking up the tenant.

    Returns:
        The authenticated Tenant object.

    Raises:
        HTTPException: If credentials are invalid, missing, or guest access is disabled.
    """
    if not token:
        if ALLOW_GUEST_ACCESS:
            set_current_tenant_id("guest")
            guest_tenant = db.query(Tenant).filter(Tenant.id == "guest").first()
            if not guest_tenant:
                guest_tenant = Tenant(id="guest", name="Guest Tenant", api_key_hash="")
                db.add(guest_tenant)
                db.commit()
                db.refresh(guest_tenant)
            return guest_tenant
        raise HTTPException(status_code=401, detail="Not authenticated")

    # The token is the plaintext API key (e.g. tenant_t123_secret)
    # Check if we can extract a tenant ID prefix
    if token.startswith("tenant_"):
        parts = token.split("_")
        if len(parts) >= 2:
            tenant_id = parts[1]

            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                if tenant.api_key_hash and verify_key(token, str(tenant.api_key_hash)):
                    set_current_tenant_id(str(tenant.id))
                    return tenant

    raise HTTPException(status_code=401, detail="Invalid authorization token")


RATE_LIMIT_MAX_REQUESTS = 100  # allow up to 100 requests per minute by default
RATE_LIMIT_WINDOW = 60.0  # 60 seconds


def rate_limit_tenant(
    tenant: Tenant = Depends(authenticate_tenant), db: Session = Depends(get_db)
) -> Tenant:
    """Enforces per-tenant rate limiting using database persistence.

    Locks the tenant row to serialize concurrent requests and checks window counts.

    Args:
        tenant: The authenticated Tenant whose rate limit is checked.
        db: Database session.

    Returns:
        The same Tenant instance if within rate limits.

    Raises:
        HTTPException: If the requests count exceeds the allowed threshold.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Serialize concurrent requests per tenant: locking the tenant row makes
    # the count+insert below atomic across workers (no-op on SQLite, which is
    # single-writer anyway).
    db.query(Tenant).filter(Tenant.id == tenant.id).with_for_update().first()

    # Delete this tenant's stale entries (per-tenant, not a global sweep)
    db.query(RateLimitEntry).filter(
        RateLimitEntry.tenant_id == tenant.id,
        RateLimitEntry.timestamp < window_start,
    ).delete()

    # Count current active entries for this tenant
    count = (
        db.query(RateLimitEntry)
        .filter(
            RateLimitEntry.tenant_id == tenant.id,
            RateLimitEntry.timestamp >= window_start,
        )
        .count()
    )

    if count >= RATE_LIMIT_MAX_REQUESTS:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Maximum 100 requests per minute allowed.",
        )

    entry = RateLimitEntry(tenant_id=tenant.id, timestamp=now)
    db.add(entry)
    db.commit()
    return tenant

