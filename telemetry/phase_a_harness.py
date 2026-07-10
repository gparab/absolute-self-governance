"""Phase A: real-infrastructure exercise of the ASG webhook app.

Real Postgres, real uvicorn (2 workers), real HMAC signatures.
Emits JSON telemetry to stdout.
"""

import concurrent.futures
import hashlib
import hmac
import json
import statistics
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8790"
SECRET = b"rw-secret-2026"
ADMIN = "rw-admin-key"
T = {}


def req(method, path, body=None, headers=None):
    data = json.dumps(body).encode() if isinstance(body, dict) else body
    r = urllib.request.Request(BASE + path, data=data, method=method,
                               headers=headers or {})
    if data is not None:
        r.add_header("Content-Type", "application/json")
    start = time.time()
    try:
        with urllib.request.urlopen(r, timeout=30) as res:
            return res.status, json.loads(res.read() or b"{}"), time.time() - start
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), time.time() - start


def signed(payload, event):
    body = json.dumps(payload).encode()
    sig = hmac.new(SECRET, body, hashlib.sha256).hexdigest()
    return body, {"X-GitHub-Event": event, "X-Hub-Signature-256": f"sha256={sig}"}


# 1. Security checks
T["tenants_without_admin_key"] = req("POST", "/tenants", {"name": "x"})[0]
status, tenant, _ = req("POST", "/tenants", {"name": "RealWorld Tenant"},
                        {"X-Admin-Key": ADMIN})
T["tenants_with_admin_key"] = status
api_key = tenant["api_key"]
auth = {"Authorization": f"Bearer {api_key}"}

T["webhook_unsigned"] = req("POST", "/webhook", {"a": 1},
                            {"X-GitHub-Event": "ping", **auth})[0]
body, h = signed({}, "ping")
T["webhook_bad_signature"] = req(
    "POST", "/webhook", body,
    {**h, "X-Hub-Signature-256": "sha256=deadbeef", **auth})[0]
T["webhook_signed_ping"] = req("POST", "/webhook", body, {**h, **auth})[0]
T["status_no_auth"] = req("GET", "/status")[0]
T["status_with_auth"] = req("GET", "/status", headers=auth)[0]

# 2. Real signed pull_request event (learning loop path, no LLM needed)
pr = {"action": "closed", "pull_request": {
    "merged": True, "created_at_timestamp": 100.0,
    "closed_at_timestamp": 130.0, "title": "Real-world PR"}}
body, h = signed(pr, "pull_request")
T["webhook_pr_merged"] = req("POST", "/webhook", body, {**h, **auth})[0]

# 3. Latency: 200 sequential signed pings on a fresh tenant per batch of limits
lat = []
_, t2, _ = req("POST", "/tenants", {"name": "latency-tenant"},
               {"X-Admin-Key": ADMIN})
auth2 = {"Authorization": f"Bearer {t2['api_key']}"}
body, h = signed({}, "ping")
for _ in range(90):
    s, _, dt = req("POST", "/webhook", body, {**h, **auth2})
    if s == 200:
        lat.append(dt * 1000)
q = statistics.quantiles(lat, n=100, method="inclusive")
T["ping_latency_ms"] = {"n": len(lat), "p50": round(q[49], 1),
                        "p90": round(q[89], 1), "p99": round(q[98], 1)}

# 4. Rate limiting under real concurrency across 2 uvicorn workers
_, t3, _ = req("POST", "/tenants", {"name": "ratelimit-tenant"},
               {"X-Admin-Key": ADMIN})
auth3 = {"Authorization": f"Bearer {t3['api_key']}"}
codes = []
with concurrent.futures.ThreadPoolExecutor(max_workers=24) as ex:
    futs = [ex.submit(req, "GET", "/status", None, auth3) for _ in range(130)]
    codes = [f.result()[0] for f in futs]
T["rate_limit_130_concurrent"] = {
    "ok_200": codes.count(200), "limited_429": codes.count(429),
    "other": len([c for c in codes if c not in (200, 429)])}

print(json.dumps(T, indent=2))
