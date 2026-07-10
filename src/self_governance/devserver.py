"""Local dev/monitoring server for `self-governance dev`.

Serves a live status page, JSON status, and Prometheus metrics on
localhost only. This is a developer tool, not the production webhook app.
"""

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, Response
from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST
from self_governance.learning import get_learning_state

dev_app = FastAPI(title="ASG Dev Monitor")


def _metric_value(name: str) -> float:
    for metric in REGISTRY.collect():
        for sample in metric.samples:
            if sample.name == name:
                return sample.value
    return 0.0


@dev_app.get("/health")
def health():
    return {"status": "ok"}


@dev_app.get("/metrics")
def metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@dev_app.get("/status")
def status():
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
    return HTMLResponse(_PAGE)
