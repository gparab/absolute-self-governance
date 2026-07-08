import os
import hmac
import hashlib
import logging
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import Response, HTMLResponse
from sqlalchemy.orm import Session
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from self_governance.nudger import ContinuousNudger
from self_governance.config import OrchestratorConfig
from self_governance.learning import track_learning_feedback
from self_governance.dimensioning import dimension_swarm
from self_governance.telemetry import new_correlation_id, get_correlation_id
from self_governance.metrics import ASG_WEBHOOK_EVENTS
from self_governance.db import init_db, get_db, Tenant, SuccessionSession, TokenUsage
from self_governance.auth import authenticate_tenant
from self_governance.billing import record_usage

# Initialize database schema
init_db()

logger = logging.getLogger("self_governance.github_app")
app = FastAPI(title="Self-Governing Software Factory App")

@app.middleware("http")
async def add_correlation_id(request: Request, call_next):
    _ = request.headers.get("X-Correlation-ID") or new_correlation_id()
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = get_correlation_id()
    return response

@app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

@app.get("/dashboard", response_class=HTMLResponse)
def get_dashboard(
    tenant: Tenant = Depends(authenticate_tenant),
    db: Session = Depends(get_db)
):
    sessions = db.query(SuccessionSession).filter(SuccessionSession.tenant_id == tenant.id).order_by(SuccessionSession.id.desc()).all()
    usages = db.query(TokenUsage).filter(TokenUsage.tenant_id == tenant.id).all()
    
    total_cost = sum(u.cost_usd for u in usages)
    total_tokens = sum(u.prompt_tokens + u.completion_tokens for u in usages)
    
    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()
        
    rows_html = ""
    for s in sessions:
        status_class = "status-completed" if s.status == "COMPLETED" else "status-pending"
        rows_html += f"""
        <tr>
            <td>#{s.id}</td>
            <td>{s.created_at.strftime('%Y-%m-%d %H:%M:%S')}</td>
            <td>
                <span class="session-status {status_class}">
                    {s.status}
                </span>
            </td>
            <td>{s.approved_roster or 'N/A'}</td>
            <td>{s.temperature} / {s.threshold}</td>
        </tr>
        """
    if not sessions:
        rows_html = """<tr><td colspan="5" style="text-align: center; color: var(--text-secondary);">No sessions recorded.</td></tr>"""
        
    html = html.replace("{{ tenant_id }}", tenant.id)
    html = html.replace("{{ tenant_stripe_id }}", tenant.stripe_customer_id or "N/A")
    html = html.replace("{{ total_cost }}", f"{total_cost:.6f}")
    html = html.replace("{{ total_tokens }}", str(total_tokens))
    html = html.replace("<!-- SESSION_ROWS -->", rows_html)
    
    return HTMLResponse(content=html)

# Load configuration and watcher context
config = OrchestratorConfig()
nudger = ContinuousNudger(working_directory=".", config=config)

# Startup check
if os.getenv("TESTING") != "True" and not os.getenv("WEBHOOK_SECRET"):
    raise ValueError("WEBHOOK_SECRET environment variable is required.")

async def verify_signature(request: Request):
    """Verify GitHub webhook HMAC signature."""
    secret = os.getenv("WEBHOOK_SECRET")
    if os.getenv("TESTING") == "True" and not secret:
        return
        
    if not secret:
        raise HTTPException(status_code=500, detail="WEBHOOK_SECRET is not configured.")
        
    signature = request.headers.get("X-Hub-Signature-256")
    if not signature:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
    body = await request.body()
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid signature")

@app.post("/webhook")
async def github_webhook(
    request: Request,
    tenant: Tenant = Depends(authenticate_tenant),
    db: Session = Depends(get_db)
):
    """
    Handle GitHub webhook payloads.
    """
    await verify_signature(request)
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Malformed JSON: {e}")

    event = request.headers.get("X-GitHub-Event", "ping")
    ASG_WEBHOOK_EVENTS.labels(event_type=event).inc()

    if event == "ping":
        return {"status": "ok", "msg": "pong"}

    if event == "issues":
        action = payload.get("action")
        if action == "opened":
            issue = payload.get("issue", {})
            title = issue.get("title", "")
            body = issue.get("body", "")
            logger.info("Processing new GitHub issue: %s", title)

            # Analyze task complexity based on simple keyword heuristics
            req_vector = [1.0, 1.0]
            if "performance" in title.lower() or "perf" in body.lower():
                req_vector[0] = 5.0
            if "security" in title.lower() or "cve" in body.lower():
                req_vector[1] = 4.0

            # Dynamic staffing: Staffing size is directly determined by the complexity vector
            transition_matrix = [[1.0, 0.0], [0.0, 1.0], [0.5, 0.5], [0.2, 0.8]]
            swarm_config = dimension_swarm(req_vector, transition_matrix)
            candidates = [agent.role for agent in swarm_config.swarm]

            # Trigger succession planning and dimensioning
            nudger.trigger_succession(f"status: COMPLETED\ncandidates: {candidates}")

            # Log succession session to database
            sess = SuccessionSession(
                tenant_id=tenant.id,
                status="COMPLETED",
                approved_roster=",".join(candidates),
                temperature=1.0,
                threshold=8.0
            )
            db.add(sess)
            db.commit()

            # Record simulated token usage for the run
            record_usage(
                tenant_id=tenant.id,
                prompt_tokens=500,
                completion_tokens=250,
                cost_usd=(500 * 0.000000075) + (250 * 0.00000030),
                db=db
            )

            return {"status": "success", "msg": "Swarm dispatched", "requirements": req_vector, "candidates": candidates}

    if event == "pull_request":
        action = payload.get("action")
        if action == "closed":
            pr = payload.get("pull_request", {})
            merged = pr.get("merged", False)
            if merged:
                # Merge completed: run the learning loop to adjust matrix weights
                cycle_time = pr.get("closed_at_timestamp", 10.0) - pr.get("created_at_timestamp", 0.0)
                sec_vulnerability = "security" in pr.get("title", "").lower()
                
                track_learning_feedback(cycle_time=cycle_time, success=True, security_breached=sec_vulnerability)
                return {"status": "success", "msg": "PR merge processed, learning loop updated."}

    return {"status": "ignored", "msg": f"Event {event} {payload.get('action')} not processed"}
