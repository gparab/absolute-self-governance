"""Guided setup: provision a tenant and (optionally) wire the GitHub webhook
for it, replacing the manual "curl the admin API, then click through GitHub
settings by hand" path with one command.
"""

import json
import logging
import secrets
import urllib.request
import urllib.error
from typing import Any, Dict, Optional

from self_governance.db import SessionLocal, Tenant, init_db
from self_governance.auth import hash_key

logger = logging.getLogger("self_governance.onboarding")

GITHUB_API = "https://api.github.com"


def generate_webhook_secret() -> str:
    """A fresh, sufficiently random secret for HMAC-signing webhook payloads."""
    return secrets.token_hex(32)


def provision_tenant(name: str) -> Dict[str, str]:
    """Creates a tenant row with a hashed API key, same as the /tenants admin
    endpoint, but callable directly from the CLI without a running server.

    Returns the tenant_id and the plaintext api_key -- the only time the
    plaintext value exists; only its hash is ever stored.
    """
    init_db()
    db = SessionLocal()
    try:
        tenant_id = "t" + secrets.token_hex(4)
        secret_key = secrets.token_hex(16)
        api_key = f"tenant_{tenant_id}_{secret_key}"
        tenant = Tenant(id=tenant_id, name=name, api_key_hash=hash_key(api_key))
        db.add(tenant)
        db.commit()
        return {"tenant_id": tenant_id, "api_key": api_key}
    finally:
        db.close()


def register_github_webhook(
    owner: str,
    repo: str,
    github_token: str,
    webhook_url: str,
    webhook_secret: str,
) -> Dict[str, Any]:
    """Creates the repo webhook via the GitHub API instead of asking the user
    to click through Settings -> Webhooks by hand.

    Raises RuntimeError with the GitHub API's own error body on failure --
    the caller decides whether to fall back to manual instructions.
    """
    url = f"{GITHUB_API}/repos/{owner}/{repo}/hooks"
    body = {
        "name": "web",
        "active": True,
        "events": ["issues", "pull_request"],
        "config": {
            "url": webhook_url,
            "content_type": "json",
            "secret": webhook_secret,
        },
    }
    headers = {
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310
            return dict(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as he:
        raise RuntimeError(
            f"GitHub API error {he.code}: {he.read().decode()}"
        ) from he


def run_onboarding(
    tenant_name: str,
    repo: Optional[str] = None,
    github_token: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Dict[str, Any]:
    """Orchestrates the full guided setup and returns a structured result the
    CLI renders into copy-pasteable steps.

    Always provisions a tenant and a webhook secret. Auto-registers the
    GitHub webhook only when repo, github_token, and base_url are all given
    -- otherwise returns manual instructions for the operator to follow,
    since a webhook target needs to be a URL GitHub can actually reach
    (tunnel, deployed host), which the CLI cannot assume.
    """
    tenant = provision_tenant(tenant_name)
    webhook_secret = generate_webhook_secret()

    result: Dict[str, Any] = {
        "tenant_id": tenant["tenant_id"],
        "api_key": tenant["api_key"],
        "webhook_secret": webhook_secret,
        "webhook_auto_registered": False,
    }

    if repo and github_token and base_url:
        owner, _, repo_name = repo.partition("/")
        webhook_url = base_url.rstrip("/") + "/webhook"
        try:
            hook = register_github_webhook(
                owner, repo_name, github_token, webhook_url, webhook_secret
            )
            result["webhook_auto_registered"] = True
            result["webhook_id"] = hook.get("id")
            result["webhook_url"] = webhook_url
        except RuntimeError as e:
            logger.warning("Automatic webhook registration failed: %s", e)
            result["webhook_registration_error"] = str(e)

    return result
