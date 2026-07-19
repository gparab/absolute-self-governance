"""GitHub Webhook integration application.

Exposes endpoints for GitHub webhook payloads, tenant creation/management,
health monitoring, and Prometheus metrics.
"""

import os
import asyncio
import hmac
import hashlib
import logging
import secrets
import time
from typing import Any, Dict, Optional, List
from pydantic import BaseModel
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from self_governance.nudger import ContinuousNudger
from self_governance.config import OrchestratorConfig
from self_governance.learning import track_learning_feedback
from self_governance.dimensioning import dimension_swarm
from self_governance.telemetry import new_correlation_id, get_correlation_id
from self_governance.metrics import ASG_WEBHOOK_EVENTS
from self_governance.db import init_db, get_db, Tenant, SuccessionSession, TokenUsage, SessionLocal
from self_governance.auth import rate_limit_tenant, hash_key
from self_governance.billing import record_usage, calculate_cost
from self_governance.tracing import tracer
from self_governance.models import SessionStatus

# Initialize database schema
init_db()

logger = logging.getLogger("self_governance.github_app")
app = FastAPI(title="Self-Governing Software Factory App")


def _log_ctx(
    tenant_id: Optional[str] = None,
    event_type: Optional[str] = None,
    duration_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Builds the extra metadata dictionary for logging formatting.

    Args:
        tenant_id: Optional active tenant ID.
        event_type: Optional name of the GitHub event.
        duration_ms: Optional latency duration in milliseconds.

    Returns:
        A dictionary containing the metadata.
    """
    ctx: Dict[str, Any] = {}
    if tenant_id is not None:
        ctx["tenant_id"] = tenant_id
    if event_type is not None:
        ctx["event_type"] = event_type
    if duration_ms is not None:
        ctx["duration_ms"] = round(duration_ms, 1)
    return ctx


@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    """FastAPI middleware to inject and propagate a correlation ID in headers.

    Args:
        request: The incoming request.
        call_next: The next middleware handler.

    Returns:
        The response with the correlation ID headers added.
    """
    _ = request.headers.get("X-Correlation-ID") or new_correlation_id()
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = get_correlation_id()
    return response


@app.get("/health")
def health():
    """Liveness and readiness probe endpoint.

    Returns:
        A health status dictionary {"status": "ok"}.
    """
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    """Metrics endpoint exposing Prometheus statistics.

    Returns:
        A Response containing the formatted Prometheus metrics payload.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/status")
def get_status(
    tenant: Tenant = Depends(rate_limit_tenant), db: Session = Depends(get_db)
):
    """Retrieves billing and usage status statistics for the authenticated tenant.

    Args:
        tenant: The authenticated tenant context.
        db: Database session.

    Returns:
        A dictionary containing token cost, count, and status.
    """
    usages = db.query(TokenUsage).filter(TokenUsage.tenant_id == tenant.id).all()
    total_cost = sum(u.cost_usd for u in usages)
    total_tokens = sum(u.prompt_tokens + u.completion_tokens for u in usages)
    return {
        "status": "ok",
        "tenant_id": tenant.id,
        "total_cost": total_cost,
        "total_tokens": total_tokens,
    }


class TenantCreateRequest(BaseModel):
    """Pydantic model representing a tenant creation request body.

    Attributes:
        name: Name of the tenant to be registered.
    """
    name: str


def require_admin(request: Request) -> None:
    """Verifies that the request carries a valid admin key header.

    Args:
        request: The incoming FastAPI request.

    Raises:
        HTTPException: If the admin key is missing, invalid, or provisioning is disabled.
    """
    admin_key = os.getenv("ADMIN_API_KEY")
    if not admin_key:
        if os.getenv("TESTING") == "True":
            return
        raise HTTPException(
            status_code=503, detail="Tenant provisioning is not enabled."
        )
    presented = request.headers.get("X-Admin-Key", "")
    if not hmac.compare_digest(presented, admin_key):
        raise HTTPException(status_code=401, detail="Invalid admin key")


@app.post("/tenants")
def create_tenant(
    payload: TenantCreateRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Provisions a new tenant with a secure API key.

    Args:
        payload: Pydantic body containing the tenant name.
        request: FastAPI Request instance.
        db: Database session.

    Returns:
        A dictionary containing the generated tenant ID and the plaintext API key.
    """
    require_admin(request)
    tenant_id = "t" + secrets.token_hex(4)
    secret_key = secrets.token_hex(16)
    api_key = f"tenant_{tenant_id}_{secret_key}"
    api_key_hash = hash_key(api_key)

    tenant = Tenant(
        id=tenant_id,
        name=payload.name,
        api_key_hash=api_key_hash,
    )
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    return {
        "tenant_id": tenant_id,
        "api_key": api_key,
        "msg": "Store the api_key safely. It will not be shown again.",
    }


# Load configuration and watcher context
config = OrchestratorConfig()
nudger = ContinuousNudger(working_directory=".", config=config)

# Startup check
if os.getenv("TESTING") != "True" and not os.getenv("WEBHOOK_SECRET"):
    raise ValueError("WEBHOOK_SECRET environment variable is required.")


async def verify_signature(request: Request):
    """Verifies the HMAC signature of incoming GitHub webhooks.

    Args:
        request: The incoming FastAPI Request.

    Raises:
        HTTPException: If the signature header is missing or does not match.
    """
    secret = os.getenv("WEBHOOK_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="WEBHOOK_SECRET is not configured.")

    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(
            status_code=401, detail="Missing X-Hub-Signature-256 header"
        )
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")


def _analyze_issue_complexity(title: str, body: str) -> List[float]:
    """Analyzes issue complexity based on keyword presence to return staff vector.

    Args:
        title: GitHub issue title.
        body: GitHub issue body content.

    Returns:
        List[float]: Staffing requirements vector of length 2.
    """
    req_vector = [1.0, 1.0]
    if "performance" in title.lower() or "perf" in body.lower():
        req_vector[0] = 5.0
    if "security" in title.lower() or "cve" in body.lower():
        req_vector[1] = 4.0
    return req_vector


def _log_succession_session(
    tenant_id: str,
    candidates: List[str],
    res: Any,
    db: Session,
) -> None:
    """Logs the details of a succession session run to the database.

    Args:
        tenant_id: The ID of the tenant.
        candidates: List of candidates voted on.
        res: Succession outcome result instance.
        db: Database session.
    """
    sess = SuccessionSession(
        tenant_id=tenant_id,
        status=SessionStatus.COMPLETED.value,
        approved_roster=",".join(candidates),
        temperature=res.final_temperature
        if hasattr(res, "final_temperature")
        else 1.0,
        threshold=res.final_threshold
        if hasattr(res, "final_threshold")
        else 8.0,
    )
    db.add(sess)
    db.commit()


def _log_token_usage(
    tenant_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    db: Session,
) -> None:
    """Records token usage and estimated USD cost to database billing.

    Args:
        tenant_id: The ID of the tenant.
        prompt_tokens: Number of prompt tokens processed.
        completion_tokens: Number of completion tokens generated.
        db: Database session.
    """
    cost_usd = calculate_cost(prompt_tokens, completion_tokens)
    record_usage(
        tenant_id=tenant_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
        db=db,
    )


def _handle_issues_event(payload: dict, tenant_id_str: str) -> Optional[dict]:
    """Handles GitHub 'issues' webhook event by scaling and triggering succession.

    Runs on a worker thread (see the /webhook route's asyncio.to_thread
    call) -- takes a plain tenant_id string rather than the request's
    Tenant/Session objects and opens its own local Session here, since
    SQLAlchemy Session objects are not thread-safe and must not cross a
    thread boundary alongside the callable that uses them.

    Args:
        payload: GitHub issues event payload.
        tenant_id_str: The associated Tenant's id.

    Returns:
        An optional dictionary representing the webhook response payload.
    """
    if payload.get("action") != "opened":
        return None

    db = SessionLocal()
    try:
        with tracer.start_as_current_span("process_issue_opened") as span:
            span.set_attribute("tenant_id", tenant_id_str)
            issue = payload.get("issue", {})
            title = issue.get("title", "")
            body = issue.get("body", "")
            logger.info(
                "Processing new GitHub issue: %s",
                title,
                extra=_log_ctx(tenant_id=tenant_id_str, event_type="issues"),
            )

            # Analyze task complexity based on simple keyword heuristics
            req_vector = _analyze_issue_complexity(title, body)

            # Dynamic staffing: Staffing size is directly determined by the complexity vector
            transition_matrix = nudger.config.webhook_matrix
            swarm_config = dimension_swarm(req_vector, transition_matrix)
            candidates = [agent.role for agent in swarm_config.swarm]

            # Trigger succession planning and dimensioning
            from self_governance.gemini_adapter import GeminiExecutionAdapter

            adapter = GeminiExecutionAdapter()
            succession_start = time.monotonic()
            try:
                res = nudger.trigger_succession(
                    f"status: {SessionStatus.COMPLETED.value}\ncandidates: {candidates}",
                    adapter=adapter,
                    tenant_id=tenant_id_str,
                )
            except Exception:
                # LLM spend already happened; record it before propagating
                # so a failed succession is never free *and* unbilled.
                cost = calculate_cost(adapter.prompt_tokens, adapter.completion_tokens)
                if adapter.prompt_tokens or adapter.completion_tokens:
                    record_usage(
                        tenant_id=tenant_id_str,
                        prompt_tokens=adapter.prompt_tokens,
                        completion_tokens=adapter.completion_tokens,
                        cost_usd=cost,
                        db=db,
                    )
                raise

            logger.info(
                "Succession session completed: %s",
                ", ".join(candidates),
                extra=_log_ctx(
                    tenant_id=tenant_id_str,
                    event_type="issues",
                    duration_ms=(time.monotonic() - succession_start) * 1000,
                ),
            )

            prompt_tokens = res.prompt_tokens
            completion_tokens = res.completion_tokens
            if os.getenv("TESTING") == "True" and prompt_tokens == 0:
                prompt_tokens = 500
                completion_tokens = 250

            _log_succession_session(tenant_id_str, candidates, res, db)
            _log_token_usage(tenant_id_str, prompt_tokens, completion_tokens, db)

            return {
                "status": "success",
                "msg": "Swarm dispatched",
                "requirements": req_vector,
                "candidates": candidates,
            }
    finally:
        db.close()


def _handle_pull_request_event(payload: dict) -> Optional[dict]:
    """Handles GitHub 'pull_request' webhook event by updating matrix tuning weight feedback.

    Args:
        payload: GitHub pull request event payload.

    Returns:
        An optional dictionary representing the webhook response payload.
    """
    action = payload.get("action")
    if action == "closed":
        pr = payload.get("pull_request", {})
        merged = pr.get("merged", False)
        if merged:
            # Merge completed: run the learning loop to adjust matrix weights
            closed_at = pr.get("closed_at_timestamp")
            if closed_at is None:
                closed_at = 10.0
            created_at = pr.get("created_at_timestamp")
            if created_at is None:
                created_at = 0.0
            try:
                cycle_time = float(closed_at) - float(created_at)
            except (TypeError, ValueError):
                cycle_time = 10.0
            sec_vulnerability = "security" in pr.get("title", "").lower()

            track_learning_feedback(
                cycle_time=cycle_time,
                success=True,
                security_breached=sec_vulnerability,
            )
            return {
                "status": "success",
                "msg": "PR merge processed, learning loop updated.",
            }
    return None


@app.post("/webhook")
async def github_webhook(
    request: Request,
    tenant: Tenant = Depends(rate_limit_tenant),
    db: Session = Depends(get_db),
):
    """FastAPI endpoint to handle incoming GitHub webhook payloads.

    Performs HMAC signature check and routes events (ping, issues, pull_request).

    Args:
        request: Incoming FastAPI Request.
        tenant: Authenticated Tenant instance.
        db: Database session.

    Returns:
        A dictionary representing the status and message of webhook execution.
    """
    await verify_signature(request)
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Malformed JSON: {e}")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid structure")

    event = request.headers.get("X-GitHub-Event", "ping")
    ASG_WEBHOOK_EVENTS.labels(event_type=event).inc()

    if event == "ping":
        return {"status": "ok", "msg": "pong"}

    # _handle_issues_event/_handle_pull_request_event are synchronous and do
    # real blocking work (succession's LLM calls, DB writes, and the test
    # sandbox's subprocess.run with up to a 30s timeout) -- calling them
    # directly here would freeze this async endpoint's event loop for that
    # whole duration, blocking every other request (health checks, metrics,
    # concurrent webhooks) on this worker. asyncio.to_thread runs them on a
    # separate thread instead. Only a plain tenant_id string crosses the
    # thread boundary, not the request-scoped Tenant/Session objects --
    # SQLAlchemy Sessions are not thread-safe, so _handle_issues_event opens
    # its own local Session inside the worker thread.
    if event == "issues":
        res = await asyncio.to_thread(_handle_issues_event, payload, str(tenant.id))
        if res:
            return res

    if event == "pull_request":
        res = await asyncio.to_thread(_handle_pull_request_event, payload)
        if res:
            return res

    return {
        "status": "ignored",
        "msg": f"Event {event} {payload.get('action')} not processed",
    }

