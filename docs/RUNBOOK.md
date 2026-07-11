# Absolute Self-Governance (ASG) Incident Runbook

This runbook covers procedures for responding to common production incidents related to ASG infrastructure, LLM quotas, webhooks, and database corruption.

## 1. Quota Exhaustion

ASG relies heavily on LLM providers. Quota exhaustion generally falls into two categories:

### A. Provider API Rate Limit (e.g., 429 Too Many Requests)
**Symptoms:** 
- Intermittent failures in `GeminiExecutionAdapter` with `429` errors.
- Telemetry shows high latencies on `asg_pipeline_latency_seconds`.
- Check `asg_session.json` or `monitoring_events.ndjson` for rate limit traces.

**Resolution:**
- **Automated Mitigation:** The adapter's `tenacity` retry logic handles short-term bursts using exponential backoff.
- **Manual Action:** If persistent, switch the model tier in `config.yaml` to a lower-tier (e.g., Flash instead of Pro) or wait for the quota window to reset. Ensure `test_mode` or dummy modes are used for local debugging.

### B. Provider Billing Exhaustion (Hard Quota Lockout)
**Symptoms:**
- Persistent `403` or `429` errors pointing to quota/billing limits across all calls.
- Benchmark or pipeline abruptly halts on all agents.

**Resolution:**
- Pause the continuous nudger: `pkill -f "self-governance run-nudger"`.
- Provision a new `GEMINI_API_KEY` with available billing limits, or switch to an alternate provider endpoint via `config.yaml`.
- Restart the nudger: `uv run self-governance run-nudger`.

## 2. Webhook Failures

**Symptoms:**
- FastAPI endpoints drop requests.
- `asg_webhook_events_total` metric flatlines.
- GitHub App webhook deliveries show failures/timeouts in the GitHub UI.

**Resolution:**
- Inspect application logs via `journalctl` or docker logs.
- If processing is blocking the event loop, ensure payload processing is delegated to Celery/background tasks.
- If the port is unresponsive, restart the `devserver.py` service.
- Check payload signatures: if secrets rotated, update the env vars and restart.

## 3. Database Corruption & Backup/Restore

ASG uses an SQLite database `self_governance.db`.

**Symptoms:**
- `sqlite3.DatabaseError: database disk image is malformed`.
- Unhandled exceptions during ORM `db.commit()` in `billing.py` or `auth.py`.

### Backup Procedure

To safely backup the database without taking the app down, use the `.backup` command in `sqlite3`:
```bash
sqlite3 self_governance.db ".backup 'self_governance_backup.db'"
```

### Restore Procedure

If the main database is corrupted:
1. Stop all ASG services (nudger, devserver).
```bash
pkill -f "self-governance"
```
2. Move the corrupted DB aside.
```bash
mv self_governance.db self_governance_corrupt.db
```
3. Restore the backup copy.
```bash
cp self_governance_backup.db self_governance.db
```
4. Run migrations/initializations if necessary, then restart the services.
```bash
uv run self-governance run-nudger &
uv run self-governance dev &
```

**Testing the Restore:**
You can test the restore process by creating a dummy record, backing up, deleting the DB, restoring, and verifying the record exists using `sqlite3 self_governance.db "SELECT * FROM ...;"`.
