# Commercial readiness design — evidence-gated path

Date: 2026-07-10
Status: approved (session discussion)
Model: self-serve SaaS for solo devs / small teams, evidence-first.
Executor: Sonnet agents, human review at every merge. Budget assumption:
existing infrastructure only; API spend within the $50 Gemini cap.

## Context

The repo is largely production-ready (security hardening, multi-tenancy,
Stripe metering, mypy zero-debt, ≥90% coverage, CI green, PyPI/GHCR
releases). The open commercial blocker is evidence: our own benchmark
(n=5, 6 tasks) showed ASG mode at parity with baseline (26/30 vs 25/30)
at 3.6x cost. A 30-rep checkpointed sweep is in progress (34 + 30 units
done across free/paid keys; resumes on daily quota reset).

"Unicorn" is a market outcome, not a deliverable. This design targets:
credible, honest, self-serve commercial product. Nothing here fabricates
or overstates claims; that constraint is inherited from this repo's
history (fabricated benchmark numbers were found and removed; see
CONTRIBUTING.md "claims need evidence").

## M1 — Evidence (gates everything else)

- **M1.1 Finish the sweep.** Resume `run_benchmark_parallel` with
  `resume_path` when the `gemini-2.5-flash` daily quota resets. Harness,
  resume-skip-errors logic (79fc88a), and per-query cost log already
  exist and are tested. Human item: keep the spend cap funded.
- **M1.2 Analysis script** `telemetry/analyze_sweep.py`: per-task
  pass-rate deltas with two-proportion confidence intervals (Wilson or
  Newcombe), cost and latency ratios, plain-language per-task verdict.
  Committed and re-runnable against the checkpoint JSONL; no
  hand-computed numbers anywhere downstream.
- **M1.3 Publish the result, whichever way it lands.** Update paper
  §4.7 and README. Verdict → consequence table:
  - ASG wins on some task class → M2 markets that class as the use case.
  - Tie/loss everywhere → M2 pivots: product becomes the orchestration
    infrastructure (TETD consensus, entropy dimensioning, sandboxed
    pipeline, checkpointed benchmark harness) positioned as library/API;
    all "better code quality" claims removed from README/paper/site.
  - Mixed or underpowered → extend reps only for ambiguous tasks.

**Gate:** every M2 task cites M1.3's verdict in its plan header.

## M2 — Self-serve loop

- **M2.1 Onboarding:** GitHub App install → first webhook → first
  automated PR with a guided setup page (replaces manual config).
  Verify: a fresh test org completes the loop end-to-end.
- **M2.2 Free-tier quotas:** per-tenant monthly run caps enforced in the
  auth/billing path. Infrastructure (tenants, metering) exists; caps do
  not. Verify: tests prove a capped tenant is rejected cleanly with a
  clear error, and an uncapped tenant is unaffected.
- **M2.3 Tenant dashboard:** extend the devserver status page to
  per-tenant usage/cost/history behind existing auth.
- **M2.4 Config wiring fix:** `GeminiExecutionAdapter` constructs
  `OrchestratorConfig()` with no path, so `config.yaml` model settings
  never reach it (hit live on 2026-07-10). Wire the config path through,
  and delete or genuinely wire `agency_agents_adapter` personas
  (currently only reachable via `LazyList`, not the benchmark path).

## M3 — Commercial hardening

- **M3.1 Pricing enforcement:** Stripe paid-tier products, upgrade flow,
  dunning webhook handling (metering exists).
- **M3.2 Legal/ops scaffolding:** ToS + privacy templates explicitly
  marked "review with a lawyer — not legal advice"; incident runbook;
  backup/restore procedure with a tested restore.
- **M3.3 Launch assets:** launch post (deferred Phase 6), demo video
  script, docs site. Human items: posting, lawyer review, pricing
  sign-off, support email.

## Out of scope

Growth/marketing execution, sales motion, SSO/enterprise features
(belongs to a future org-tier design), multi-provider LLM support
(tracked as issue #3, not gating).

## Verification discipline

Every task: pytest ≥90% branch coverage, ruff, mypy, CI green, plus a
task-specific end-to-end check named in its plan. Agents report failures
verbatim. Public-surface actions (releases, tags, third-party posts)
require explicit human go-ahead.
