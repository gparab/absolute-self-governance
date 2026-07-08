import contextvars
import hmac
import hashlib
import os
from fastapi import Request, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from self_governance.db import get_db, Tenant

tenant_id_var = contextvars.ContextVar("tenant_id", default="")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

ALLOW_GUEST_ACCESS = os.getenv("ALLOW_GUEST_ACCESS", "False").lower() in ("true", "1", "yes") or os.getenv("TESTING") == "True"

def get_current_tenant_id() -> str:
    return tenant_id_var.get()

def set_current_tenant_id(tenant_id: str) -> None:
    tenant_id_var.set(tenant_id)

def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

async def authenticate_tenant(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Tenant:
    """Authenticate incoming HTTP request against Tenant API keys/tokens."""
    if not token:
        if ALLOW_GUEST_ACCESS:
            guest_tenant = db.query(Tenant).filter(Tenant.id == "guest").first()
            if not guest_tenant:
                guest_tenant = Tenant(id="guest", name="Guest Tenant", api_key_hash=hash_key("guest"))
                db.add(guest_tenant)
                db.commit()
                db.refresh(guest_tenant)
            set_current_tenant_id("guest")
            return guest_tenant
        else:
            raise HTTPException(status_code=401, detail="Authentication token required")

    # Verify the presented token by hashing it and comparing against stored hash
    if token.startswith("tenant_"):
        parts = token.split("_")
        if len(parts) >= 2:
            tenant_id = parts[1]
            tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
            if tenant:
                presented_hash = hash_key(token)
                if hmac.compare_digest(tenant.api_key_hash, presented_hash):
                    set_current_tenant_id(tenant.id)
                    return tenant

    raise HTTPException(status_code=401, detail="Invalid authorization token")

import time
from collections import defaultdict

# Simple in-memory rate limiter cache: {tenant_id: [timestamps]}
rate_limit_cache = defaultdict(list)
RATE_LIMIT_MAX_REQUESTS = 100  # allow up to 100 requests per minute by default
RATE_LIMIT_WINDOW = 60.0       # 60 seconds

def rate_limit_tenant(tenant: Tenant = Depends(authenticate_tenant)) -> Tenant:
    """Enforces per-tenant rate limiting."""
    now = time.time()
    timestamps = rate_limit_cache[tenant.id]
    
    # filter out timestamps older than the window
    active_timestamps = [ts for ts in timestamps if now - ts < RATE_LIMIT_WINDOW]
    
    if len(active_timestamps) >= RATE_LIMIT_MAX_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded. Maximum 100 requests per minute allowed."
        )
        
    active_timestamps.append(now)
    rate_limit_cache[tenant.id] = active_timestamps
    return tenant
