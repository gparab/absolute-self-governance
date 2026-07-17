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

**C2a. Production evaluation harness — built.** `telemetry/eval_memory_recall.py`
(+ `tests/test_eval_memory_recall.py`) seeds a synthetic multi-tenant,
multi-session scenario into an isolated DB and checks the properties C1's
read/write loop depends on: recall, tenant isolation, feature isolation, and
(after C2b landed) A-MEM linking. 7/7 checks green as of this writing.

**C2b. Automatic fact extraction and lexical A-MEM linking — built.**
Deliberately scoped down from the book's LLM-driven Mem0/A-MEM design to
what's verifiable without a new external dependency or an unfalsifiable
"the model extracted good facts" claim:
- `src/self_governance/fact_extraction.py`: `extract_facts()` regex-parses
  the verify phase's own tool output (`pytest -q` FAILED lines, the
  security-auditor's `[SEVERITY] category` / `Description:` lines) into one
  discrete constraint per failure/finding, instead of folding everything
  into a single canned sentence. Wired into `nudger.py`'s ship phase
  alongside the existing pass/fail `reflection` summary.
- `GraphMemoryEngine.add_session_node` (graph_memory.py): every new
  constraint is linked via a bidirectional `RELATES_TO` edge to prior
  constraints (same tenant) whose token Jaccard similarity clears 0.3 --
  lexical, not embedding, similarity (no vector store in this stack, and a
  token-overlap threshold is auditable in a way a cosine cutoff over an
  opaque embedding isn't). `query_context` now does a one-hop `RELATES_TO`
  traversal, so a constraint filed under one feature can surface when
  querying a lexically related constraint on a different feature.
- Explicitly not attempted: semantic (embedding-based) similarity, and
  sleep-time offline consolidation (§17.11.3) -- both would need a concrete
  design and, for embeddings, a new dependency; recorded as still-open
  future work rather than built speculatively.

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
