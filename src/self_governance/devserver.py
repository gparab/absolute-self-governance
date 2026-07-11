"""Local dev/monitoring server for `self-governance dev`.

Serves a live status page, JSON status, and Prometheus metrics on
localhost only. This is a developer tool, not the production webhook app.
"""

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response
from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST
from self_governance.learning import get_learning_state

dev_app = FastAPI(title="ASG Dev Monitor")


def _metric_value(name: str) -> float:
    """Helper to retrieve a Prometheus metric value by its name.

    Args:
        name: The name of the Prometheus metric to query.

    Returns:
        The current float value of the sample, or 0.0 if not found.
    """
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name:
                return sample.value
    return 0.0


@dev_app.get("/health")
def health():
    """Health check endpoint for the monitor app.

    Returns:
        A dictionary containing the status string "ok".
    """
    return {"status": "ok"}


@dev_app.get("/metrics")
def metrics():
    """Metrics endpoint exposing Prometheus metrics.

    Returns:
        FastAPI Response wrapping the Prometheus plain text payload.
    """
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@dev_app.get("/status")
def status():
    """Exposes current ASG state and costs in a simple JSON payload.

    Returns:
        A dictionary of the runs metrics, tuning metrics, and session cost.
    """
    state = get_learning_state()
    return {
        "runs_completed": state["runs_completed"],
        "success_rate": state["success_rate"],
        "average_cycle_time": state["average_cycle_time"],
        "vulnerability_counts": state["vulnerability_counts"],
        "matrix_scale_factor": state["matrix_tuning"]["scale_factor"],
        "consensus_iterations": _metric_value("asg_consensus_iterations_total"),
        "session_cost_usd": _metric_value("asg_swarm_cost_usd_total"),
    }


# ponytail: one inline page polling /status; add a real frontend never.
_PAGE = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>ASG Dev Monitor</title>
<style>
  body { font-family: ui-monospace, monospace; margin: 2rem; background: #111;
         color: #ddd; }
  h1 { font-size: 1.2rem; } h1 span { color: #6c6; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 1rem; max-width: 900px; }
  .card { background: #1c1c1c; border: 1px solid #333; border-radius: 8px;
          padding: 1rem; }
  .label { color: #888; font-size: 0.75rem; text-transform: uppercase; }
  .value { font-size: 1.6rem; margin-top: 0.3rem; }
  .cost .value { color: #fc6; }
  #err { color: #f66; }
</style>
</head>
<body>
<h1>ASG Dev Monitor <span id="dot">●</span></h1>
<div class="grid">
  <div class="card cost"><div class="label">Session cost (USD)</div><div class="value" id="session_cost_usd">–</div></div>
  <div class="card"><div class="label">Runs completed</div><div class="value" id="runs_completed">–</div></div>
  <div class="card"><div class="label">Success rate</div><div class="value" id="success_rate">–</div></div>
  <div class="card"><div class="label">Avg cycle time (s)</div><div class="value" id="average_cycle_time">–</div></div>
  <div class="card"><div class="label">Consensus iterations</div><div class="value" id="consensus_iterations">–</div></div>
  <div class="card"><div class="label">Vulnerability alerts</div><div class="value" id="vulnerability_counts">–</div></div>
  <div class="card"><div class="label">Matrix scale factor</div><div class="value" id="matrix_scale_factor">–</div></div>
</div>
<p id="err"></p>
<script>
async function tick() {
  try {
    const r = await fetch('/status');
    const d = await r.json();
    for (const [k, v] of Object.entries(d)) {
      const el = document.getElementById(k);
      if (!el) continue;
      if (k === 'session_cost_usd') el.textContent = '$' + v.toFixed(5);
      else if (k === 'success_rate') el.textContent = (v * 100).toFixed(1) + '%';
      else el.textContent = typeof v === 'number' ? +v.toFixed(2) : v;
    }
    document.getElementById('err').textContent = '';
    document.getElementById('dot').style.color = '#6c6';
  } catch (e) {
    document.getElementById('err').textContent = 'disconnected: ' + e;
    document.getElementById('dot').style.color = '#f66';
  }
}
tick();
setInterval(tick, 2000);
</script>
</body>
</html>"""


@dev_app.get("/")
def index():
    """Index page exposing the HTML status page.

    Returns:
        HTMLResponse containing the live dashboard.
    """
    return HTMLResponse(_PAGE)


# === P2P Session Sharing Routes ===

@dev_app.post("/api/p2p/share")
async def p2p_create_share(request: Request):
    """Create a share token for the current agent's session.

    POST body (JSON):
    {
        "session_data": {...},   # dict of session state to share
        "ttl_seconds": 300,      # optional, default 300
        "created_by": "agent-A"  # optional identifier
    }

    Returns:
        JSON with token, expires_at, fingerprint, and created_by on success.
        JSON with error on 400 if the payload exceeds the size limit.
    """
    from self_governance.p2p import create_share_token
    from fastapi.responses import JSONResponse

    body = await request.json()
    session_data = body.get("session_data", {})
    ttl = int(body.get("ttl_seconds", 300))
    created_by = str(body.get("created_by", "unknown"))
    try:
        token = create_share_token(session_data, ttl_seconds=ttl, created_by=created_by)
        return JSONResponse({
            "token": token.token,
            "expires_at": token.expires_at,
            "fingerprint": token.fingerprint,
            "created_by": token.created_by,
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


@dev_app.get("/api/p2p/session/{token}")
async def p2p_get_session(token: str, peek: bool = False):
    """Retrieve a shared session by token.

    By default the token is consumed on first use (one-time handoff semantics).
    Pass ?peek=true to inspect token metadata without consuming it.

    Args:
        token: The share token string.
        peek: If True, return metadata only without consuming the token.

    Returns:
        JSON with session_data on success (consume mode), or token metadata
        dict in peek mode. 404 if not found or expired.
    """
    from self_governance.p2p import get_shared_session, peek_shared_session
    from fastapi.responses import JSONResponse

    if peek:
        meta = peek_shared_session(token)
        if meta is None:
            return JSONResponse({"error": "Token not found or expired"}, status_code=404)
        return JSONResponse(meta.to_dict())

    session = get_shared_session(token)
    if session is None:
        return JSONResponse(
            {"error": "Token not found, expired, or already consumed"},
            status_code=404,
        )
    return JSONResponse({"session_data": session})


@dev_app.delete("/api/p2p/session/{token}")
async def p2p_revoke_session(token: str):
    """Revoke a share token before it is consumed.

    Args:
        token: The share token string to revoke.

    Returns:
        JSON with revoked=True if the token was found and removed,
        revoked=False if the token was not found.
    """
    from self_governance.p2p import revoke_share_token
    from fastapi.responses import JSONResponse

    revoked = revoke_share_token(token)
    return JSONResponse({"revoked": revoked})


@dev_app.get("/api/p2p/tokens")
async def p2p_list_tokens():
    """List all active share tokens (metadata only, session payload never exposed).

    Returns:
        JSON with a tokens list, each entry containing token, expires_at,
        fingerprint, created_by, and ttl_remaining.
    """
    from self_governance.p2p import list_active_tokens
    from fastapi.responses import JSONResponse

    return JSONResponse({"tokens": list_active_tokens()})

