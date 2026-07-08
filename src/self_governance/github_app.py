import os
import hmac
import hashlib
import logging
from fastapi import FastAPI, Request, HTTPException
from self_governance.nudger import ContinuousNudger
from self_governance.config import OrchestratorConfig
from self_governance.learning import track_learning_feedback
from self_governance.dimensioning import dimension_swarm

logger = logging.getLogger("self_governance.github_app")
app = FastAPI(title="Self-Governing Software Factory App")

# Load configuration and watcher context
config = OrchestratorConfig()
nudger = ContinuousNudger(working_directory=".", config=config)

async def verify_signature(request: Request):
    """Verify GitHub webhook HMAC signature if secret is configured."""
    secret = os.getenv("WEBHOOK_SECRET")
    if secret:
        signature = request.headers.get("X-Hub-Signature-256")
        if not signature:
            raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
        body = await request.body()
        expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise HTTPException(status_code=401, detail="Invalid signature")

@app.post("/webhook")
async def github_webhook(request: Request):
    """
    Handle GitHub webhook payloads.
    """
    await verify_signature(request)
    try:
        payload = await request.json()
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Malformed JSON: {e}")

    event = request.headers.get("X-GitHub-Event", "ping")

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
