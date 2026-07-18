# Hardening roadmap grounded in Conway-Research/automaton

Date: 2026-07-17
Status: Planned, none of this built yet
Source: architecture study of https://github.com/Conway-Research/automaton
(MIT-licensed, ~5k stars, TypeScript). Automaton is a self-funding,
self-replicating, wallet-holding autonomous agent — most of that (on-chain
payments, ERC-8004 identity, child-spawning) is out of scope for ASG and is
explicitly excluded below, along with why. What's included is the subset of
its engineering patterns that map onto real, currently-unaddressed gaps in
this repo.

## Explicitly excluded, and why

- **Financial autonomy** (USDC wallet, x402 payment protocol, treasury
  policy, credit topup). ASG is a software-engineering swarm, not an
  economically sovereign agent. Nothing in this repo needs to pay for its
  own compute, and building that capability would be the same category of
  action this assistant declines to take unilaterally for a user — autonomous
  financial transfers are not something to add just because a reference
  implementation has it.
- **On-chain identity / ERC-8004 registry / agent discovery.** No use case
  in ASG's dynamic-roster or benchmark paths needs a public on-chain agent
  card.
- **Autonomous replication** (spawn_child, lineage, constitution
  propagation to children). ASG's roster dynamics are already handled by
  TETD consensus; spinning up literal child processes with independent
  wallets and lifecycles solves a problem ASG doesn't have.
- **SOUL.md personality evolution.** Interesting idea, no product need here
  — ASG's "identity" is its consensus roster and graph memory, not a
  first-person narrative document.

## What's included, and why each maps onto a real gap

Automaton runs every tool call through a centralized, rule-based,
audit-logged policy engine; sanitizes all external input through an
explicit injection-defense module with per-category tests; tracks named
procedures with success/failure counters as a distinct memory tier; and
backs its background scheduler with a leased, DB-durable table instead of
an in-process loop. Four of these map directly onto gaps this repo
currently papers over with `# nosec` comments, unsanitized external input,
a constraint-only memory model, and a single-process watchdog.

---

## Phase D1 — Policy engine for nudger.py

**Problem.** Every dangerous operation in `nudger.py` (git commands,
subprocess exec, writes to `handoff.md`/config/worktrees) is currently
allowed by a `# nosec B603 B607`-style comment at the call site — a human
asserted "this is fine" once, with no runtime check, no audit trail, and no
test coverage of the assertion itself. `security.py`'s OWASP+STRIDE audit
runs against *generated code*, not against ASG's own actions.

**Design.** A `PolicyEngine` class in a new `src/self_governance/policy.py`:
- `PolicyRule` protocol: `evaluate(action: PolicyAction) -> PolicyDecision | None` (None = no opinion, falls through to next rule).
- Rule categories, priority-ordered, first `deny` wins (mirrors automaton's
  design almost exactly, it fits ASG's shape too):
  1. **Path protection** — blocks writes to `constitution`-equivalent files
     (`handoff.md`, `.planning/`, config, DB) from anywhere except the
     nudger's own trusted write paths; blocks reads of secrets (API keys,
     `.env`).
  2. **Command safety** — forbidden subprocess argv patterns (`rm -rf /`,
     `git push --force` to protected branches, etc.) and a rate limit on
     self-modifying git operations (the Ship Phase's auto-commit/merge).
  3. **Source-authority** — handoff content parsed from `interrupt.md`
     (external/God's Eye input) gets a lower trust tier than the nudger's
     own generated YAML; only trusted-tier actions may trigger the Ship
     Phase's git merge.
  4. **Rate limits** — reuse the existing DoS/candidate-expansion guards
     already in `nudger.py`, just centralize them as rules instead of
     inline checks.
- Every `subprocess.run(...)` call site in the Ship Phase (nudger.py
  ~L607-618) routes through `policy_engine.check(action)` first; a `deny`
  raises instead of running. Decisions logged via the existing `_emit_event`
  NDJSON mechanism (no new persistence layer needed — ASG already has one).
- Replace the `# nosec` comments with policy rule coverage; bandit
  suppressions become "this specific class of action is allowlisted by a
  tested rule," not "a human said so."

**Deliverables:** `policy.py`, `policy_rules/` (path_protection.py,
command_safety.py, authority.py, rate_limits.py), `tests/test_policy.py`
with one test file per rule category (mirrors automaton's test layout),
nudger.py's Ship Phase subprocess calls migrated to go through it.

**Success criteria:** every subprocess/git call in the Ship Phase denies
under a synthetic malicious handoff (e.g., forced-push, path traversal in
worktree name); gates stay green; policy denials show up in
`monitoring_events.ndjson` with enough context to audit after the fact.

**Cost:** M.

---

## Phase D2 — Injection defense for external input

**Problem.** `interrupt.md` (God's Eye) content is read and injected
directly into the next `PipelineArtifact`'s `open_questions`/`next_context`
with no sanitization (nudger.py ~L438-464). Webhook tenant input
(`tenant_id`, roster candidates) is minimally validated for type/DoS but
not for injection patterns aimed at the next succession's prompt.

**Design.** `src/self_governance/injection_defense.py` with a
`sanitize(text: str, source: TrustLevel) -> SanitizationResult` scoped down
from automaton's 8 categories to the ones that actually apply to ASG's
input surface (no ChatML/multi-provider chat-format concerns since ASG's
prompt is a single drafted markdown file, not a chat transcript):
1. Instruction-override patterns ("ignore previous instructions", "you are
   now...").
2. Authority-claim patterns ("as the system administrator...", "per your
   creator's instructions...").
3. Boundary-manipulation patterns (fake trust-boundary markers mimicking
   `_emit_event`'s own JSON structure, or fake `---` YAML frontmatter
   delimiters trying to inject a second handoff block).
4. Encoding evasion (base64/hex blobs that decode to instruction patterns).
Untrusted content gets wrapped with an explicit trust-boundary marker when
injected into `next_context`, same as automaton wraps skill content — so
even sanitized-but-suspicious text is visibly quarantined in the prompt
rather than blended in as if the nudger wrote it itself.

**Deliverables:** `injection_defense.py`, `tests/test_injection_defense.py`
(one test per detection category, matching automaton's
`injection-defense.test.ts` granularity), wired into the God's Eye
interrupt path and the webhook tenant-input path.

**Success criteria:** a synthetic interrupt containing each of the 4
attack patterns is caught and quarantined, not silently blended into
`next_context`; legitimate interrupts (real constraints) pass through
unmodified; gates stay green.

**Cost:** S-M.

---

## Phase D3 — Procedural memory tier (extends C1/C2)

**Problem.** `GraphMemoryEngine` (Phase C1/C2) stores *facts* (constraints,
test failures) but nothing about *strategies that worked*. The ASG repair
loop (perspective-rotating attempts: Backend Wizard → QA Specialist →
Security Auditor) retries blind every time — it doesn't know that, say,
"when the failure shape is a boundary-condition test failure, leading with
QA Specialist resolves it faster than the default order" even after
observing that pattern across dozens of prior sessions.

**Design.** A `ProceduralMemory` addition to `graph_memory.py`, reusing the
same tenant-scoped SQLite/networkx substrate as C1/C2 rather than a new
subsystem:
- New node type `Procedure`: `{name, trigger_pattern, steps, success_count, failure_count}`.
- `record_procedure_outcome(name, trigger_pattern, passed: bool)` — called
  from the benchmark harness's repair loop (`benchmark.py`) after each
  attempt resolves, incrementing success/failure counters for the
  (failure_class, persona_order) pair that was tried.
- `recommend_procedure(trigger_pattern) -> Optional[str]` — queried before
  the repair loop picks its next persona order; returns the
  highest-success-rate strategy seen for a similar `trigger_pattern`
  (reuse the existing lexical Jaccard matching from C2b's A-MEM linking —
  no new similarity mechanism needed).
- Explicitly **not** wired into the benchmark path itself at first — per
  the standing memoization-hazard rule (C1's spec note: "benchmark path
  stays untouched"), this ships as an opt-in query the harness can call,
  validated against historical data, before any claim is made that it
  changes benchmark outcomes. Any claim that procedural memory improves
  pass rate needs its own held-out-style validation sweep before it goes
  in the paper — same discipline as §4.7.4.

**Deliverables:** procedural memory schema + methods in `graph_memory.py`,
`tests/test_graph_memory.py` additions, extend
`telemetry/eval_memory_recall.py` with procedural-recall checks (mirrors
how C2b's linking checks were added, not a new harness).

**Success criteria:** a synthetic 2-session fixture where session 2's
failure shape matches session 1's resolved failure shape recommends
session 1's successful strategy; gates stay green; no benchmark-path file
touched until a dedicated validation sweep justifies wiring it in.

**Cost:** M.

---

## Phase D4 — Metrics + alerting on top of existing telemetry

**Problem.** ASG has OTel spans (Phase B3) and `analyze_sweep.py`/
`check_regression.py`, but nothing proactive — a regression or a
consecutive-failure streak is only caught if someone remembers to run
analysis. Automaton's `AlertEngine` (cooldown-gated rules over metric
snapshots: high deny-rate, budget exhaustion, consecutive failures) is a
small, well-scoped pattern that closes exactly this gap.

**Design.** `src/self_governance/alerts.py`:
- Reuses the failure-taxonomy fields already emitted per-unit (Phase A) —
  no new metrics collection needed, just aggregation + rule evaluation
  over the existing NDJSON event stream and benchmark checkpoints.
- `AlertRule` list: consecutive `sandbox_error` streak (already causes a
  circuit-breaker trip in the benchmark harness — this generalizes the
  concept to the nudger's own event stream), Phase D1 policy-deny rate
  spike, graph-memory-recall harness regression (Phase C2a's 7 checks
  going from green to red).
- Cooldown per rule (avoid alert spam), emitted via the existing
  `_emit_event` mechanism under a new `"alert"` event type — no new
  delivery channel, this repo doesn't have one and shouldn't invent a
  notification integration speculatively.

**Deliverables:** `alerts.py`, `tests/test_alerts.py`, wired into
`nudger.py`'s existing event emission and `telemetry/analyze_sweep.py`'s
output.

**Success criteria:** a synthetic consecutive-sandbox_error checkpoint and
a synthetic memory-recall-harness failure each fire their rule exactly
once (not per-occurrence, respecting cooldown); gates stay green.

**Cost:** S.

---

## Phase D5 — Durable heartbeat scheduler (deferred, contingent)

**Problem.** `nudger.watch_handoff()` is a single-process polling loop.
Automaton's DB-backed `DurableScheduler` (leased tasks, cron expressions,
atomic wake events) would make multi-worker nudger deployment safe.

**Not scoped yet.** This is only worth building if multi-worker deployment
becomes an actual near-term goal — right now it would be process
duplication with no consumer. Recorded here so the pattern isn't
forgotten, not committed to.

---

## Suggested build order

D2 (injection defense) is the smallest, most self-contained, and closes a
currently-real unguarded input path — natural first PR. D1 (policy engine)
is the highest-value but touches more of the Ship Phase's existing
subprocess call sites, so it benefits from D2 already existing (some
policy rules can consume injection-defense verdicts as input). D3
(procedural memory) builds directly on C1/C2 and is well-isolated from the
rest. D4 (alerting) is cheapest and can slot in anywhere once D1-D3 emit
events worth alerting on. D5 stays parked until there's an actual
multi-worker deployment need.

## Success criteria (overall)

- Every phase: gates green (tests, ruff, mypy, bandit), no benchmark-path
  file touched without an explicit validation sweep justifying it, same
  discipline as Phases A-C.
- D1: a synthetic malicious Ship Phase action is denied and audited.
- D2: all 4 injection categories caught on synthetic attacks, zero false
  positives on real interrupts.
- D3: procedural recommendation surfaces correctly on a seeded fixture; no
  benchmark claim made until validated.
- D4: alerts fire once per incident, not per occurrence.
