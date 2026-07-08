import os
import logging
from fastapi import FastAPI, Request, HTTPException
from self_governance.nudger import ContinuousNudger
from self_governance.config import OrchestratorConfig
from self_governance.learning import track_learning_feedback

logger = logging.getLogger("self_governance.github_app")
app = FastAPI(title="Self-Governing Software Factory App")

# Load configuration and watcher context
config = OrchestratorConfig()
nudger = ContinuousNudger(working_directory=".", config=config)

@app.post("/webhook")
async def github_webhook(request: Request):
    """
    Handle GitHub webhook payloads.
    """
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

            # Trigger succession planning and dimensioning
            candidates = ["agent_dev", "agent_qa", "agent_sec", "agent_reviewer"]
            nudger.trigger_succession(f"status: COMPLETED\ncandidates: {candidates}")

            return {"status": "success", "msg": "Swarm dispatched", "requirements": req_vector}

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
