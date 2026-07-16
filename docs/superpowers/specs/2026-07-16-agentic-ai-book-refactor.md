# Refactor roadmap grounded in "The Hitchhiker's Guide to Agentic AI" (Roitman 2026, arXiv:2606.24937)

Date: 2026-07-16
Status: Phase A implemented; B/C planned
Source: targeted study of Ch. 19 (Agent Design Patterns), Ch. 25.5-25.6
(Agent Testing / Observability), Ch. 17.10-17.11 (Memory-Augmented Agent
Loop, Mem0/A-MEM/sleep-time compute). Chapter/table numbers cited below
refer to that text.

## What the book validates about the current design (no change needed)

- The perspective-rotating, test-verified attempt loop in `run_asg_mode`
  is the book's **evaluator-optimizer** pattern (§19.1.5), its named
  recommendation for "code that must pass tests." Generator = persona-led
  generation; evaluator = the Docker pytest sandbox; critique = the
  failure log fed to the next attempt. Budgeted at 2-10 calls in the
  book; ours runs 1-3.
- This repo's simplification history (dropping consensus annealing,
  dimensioning, and discarded review stages from the benchmark path)
  matches the book's first design principle: *"Keep it simple. A prompt
  chain that solves the problem is always preferable to a multi-agent
  system that might"* — and its "start with workflows, graduate to
  agents only when required" rule. The TETD consensus engine remains
  correctly positioned for the dynamic-roster production path only.
- Structured outputs with recovery (§19.3 principle 5): the JSON-schema
  generation contract plus the reformat retry already implement this.

## Phase A — implemented in this iteration

**A1. Failure taxonomy in the benchmark harness** (after Table 25.1).
Three sweeps in this repo's history were invalidated by infrastructure
failures recorded as ordinary FAILs — a revoked API key, a Docker daemon
down after reboot, and a broken harness shim — each caught only by a
human noticing impossible aggregate numbers after burning a day's quota.
Implemented:
- `_classify_failure()` in `benchmark.py`: every failed unit is now
  classed `sandbox_error` (environment broken — says nothing about code
  quality), `no_files_written` (generation produced nothing runnable:
  API or unrecoverable format failure), or `tests_failed` (genuine
  quality signal). Both mode functions record `failure_class`.
- The sweep circuit breaker now trips on consecutive `sandbox_error`
  units, not just checkpoint-level errors — a broken sandbox can never
  produce valid data.
- `telemetry/analyze_sweep.py` prints the failure-class distribution and
  a loud invalid-dataset warning when infrastructure classes account for
  over half of failures.
Fairness note: classification is identical for both modes and changes no
pass/fail semantics; it annotates, aborts-on-broken-environment, and
reports. Historical checkpoints without the field remain readable
(`failure_class` is absent, analyzer skips it).

## Phase B — planned, benchmark/adoption (not yet built)

**B1. Golden-trajectory regression tests** (§25.5.3). Capture one
known-good unit per mode (tool/attempt sequence, pass/fail, token cost)
as `tests/golden/*.json`; assert future runs match the trajectory shape
and stay within 1.2x cost. Requires one live capture run; deterministic
assertions must be limited to structure (sequence/attempts), not exact
text — model outputs vary. Cost: S-M.

**B2. Cost/latency bounds tests** (§25.5.5). Parametrized bounds per
task class using the existing TaskWallet accounting; complements B1.
Cost: S.

**B3. Run-level observability attributes** (§25.6.1). Current OTel spans
cover only individual API calls. Add a per-unit span carrying task_id,
mode, attempts, failure_class, cost — making sweep telemetry directly
traceable, and stop printing raw span JSON to stdout during CLI runs
(noise that buries real signals; a diagnosing agent missed a live error
inside span spam once). Cost: S.

## Phase C — planned, production memory (extends improvement-plan Phase 2)

**C1. Read-act-reflect-write loop for nudger.py** (§17.10.3, CoALA/
MemGPT pattern). The improvement plan's Phase 2 wires
`GraphMemoryEngine.query_context` as a read; the book's pattern adds the
missing half: *write* a reflection after each succession (what failed,
what fixed it — Reflexion, §19.2.3) and *reflect* periodically to
consolidate insights. Concretely: after a verified succession, write a
constraint/insight node; the existing `add_session_node` schema already
supports constraints. Benchmark path stays untouched (memoization
hazard recorded in the improvement plan stands). Cost: M.

**C2. Recorded, not built:** Mem0-style automatic fact extraction and
A-MEM dynamic linking (§17.11) — production-scale memory features that
need C1 shipping first plus a production evaluation harness before any
claims. Sleep-time compute (§17.11.3) — offline consolidation between
webhook events; architecturally natural for the nudger's idle loop, but
premature before C1.

## Adoption rationale (the "organic developer adoption" thread)

The book's testing pyramid and failure taxonomy are what make an agent
framework *trustable* enough to adopt: a developer evaluating this repo
can now see failures classified honestly, sweeps that self-abort on
broken environments, and (after B1/B2) regression tests that pin cost
and behavior. Trust through verifiable honesty is this project's
existing differentiator; Phase A-B make it mechanical rather than
narrative.

## Success criteria

- Phase A: gates green; a synthetic sandbox-down sweep aborts early
  (test-covered); analyzer flags an infrastructure-contaminated dataset
  (manually verified against the discarded Docker-down checkpoint
  pattern).
- Phase B: golden tests catch a deliberately introduced trajectory
  regression; cost bounds catch a 1.5x token inflation.
- Phase C: `query_context` returns non-empty, relevant context on a
  2-session seeded fixture; reflections accumulate across successions;
  no benchmark-path file touched.
