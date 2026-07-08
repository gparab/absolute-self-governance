import contextvars
import hmac
from fastapi import Request, HTTPException, Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from self_governance.db import get_db, Tenant

tenant_id_var = contextvars.ContextVar("tenant_id", default="")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

def get_current_tenant_id() -> str:
    return tenant_id_var.get()

def set_current_tenant_id(tenant_id: str) -> None:
    tenant_id_var.set(tenant_id)

async def authenticate_tenant(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> Tenant:
    """Authenticate incoming HTTP request against Tenant API keys/tokens."""
    if not token:
        # Fallback to a default guest tenant for local CLI/webhook requests if no auth header
        guest_tenant = db.query(Tenant).filter(Tenant.id == "guest").first()
        if not guest_tenant:
            guest_tenant = Tenant(id="guest", name="Guest Tenant", api_key_hash="guest_hash")
            db.add(guest_tenant)
            db.commit()
            db.refresh(guest_tenant)
        set_current_tenant_id("guest")
        return guest_tenant

    # Simple mock token format: "Bearer tenant_<id>_key"
    if token.startswith("tenant_"):
        parts = token.split("_")
        tenant_id = parts[1]
        tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
        if tenant:
            set_current_tenant_id(tenant.id)
            return tenant

    raise HTTPException(status_code=401, detail="Invalid authorization token")
