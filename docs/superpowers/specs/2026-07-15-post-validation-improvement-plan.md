# ASG Pipeline Improvement Plan — Post 30-Rep Validation Sweep

## 1. Evidence base (proven, with numbers)

- Source: `telemetry/analyze_sweep.py` on `telemetry/phase_f_nemotron_30rep_v2_rotating.jsonl` (360 result rows, n=30/task/mode, 6 tasks).
- **Aggregate**: BASELINE 176/180 (97.8%, CI [94.4%, 99.1%]), mean latency 20.8s. ASG 180/180 (100.0%, CI [97.9%, 100.0%]), mean latency 21.1s (1.0x baseline).
- **CIs overlap.** The analyzer itself says so. +2.2pp is not yet distinguishable from noise at n=30.
- All 4 baseline misses are on the 3 "hard"/concurrency-flavored tasks (lru_cache, retry_backoff, thread_safe_cache). The 3 easy/trivial tasks are 100% both modes — zero headroom there.
- ASG attempts distribution: {1: 173, 2: 6, 3: 1} of 180. The mechanism (perspective-rotating persona retry) resolves 96% of runs on the first try; only 7 runs ever exercise the "value-add" retry path.
- This result supersedes two earlier, worse sweeps (Gemini 2.5 Flash n=30 and an earlier Nemotron n=180 sweep, both showing ASG *losing*) that were confounded by a harness bug (dropped persona system-instructions, capped output tokens) — i.e., this exact pipeline has a recent history of being fooled by infra bugs, not just genuine effect changes. Extra skepticism warranted before claiming a durable win.
- Unused-but-built machinery confirmed by code inspection: `GraphMemoryEngine` (graph_memory.py) has exactly one caller (nudger.py), never touches benchmark.py. `ConsensusEngine`/TETD (consensus.py) is explicitly excluded from the benchmark hot path by design. `route_model`/`analyze_ast_complexity` (economics.py) is bypassed in the benchmark because both modes force a single model.

**Bottom line: the current 180/180 vs 176/180 is a real, reproducible number from a real sweep, but it's a small-n result on a saturated, easy benchmark (3 of 6 tasks contribute zero information) with overlapping CIs. Nothing below should be sold as "proven to help" — everything is scoped as an experiment against this baseline, and several items exist specifically to test whether the effect is real.**

## 2. Dropped ideas (verdict DROP) — one line each, not silently lost

- Graph-memory failure→repair retrieval (same-task-id): would leak answers across reps of the same 6-task suite (memoization, not generalization) — no held-out tasks exist to validate it.
- Graph-memory similarity-based persona reordering: GraphMemoryEngine has no failure-signature schema to support it; only ~7/180 runs ever rotate personas, no statistical power.
- Complexity-gated attempt budget (1 vs 3): loop already breaks on first pass, so it saves nothing in the 173/180 common case, and only risks converting a rescued run into a failure.
- Keyword-based persona reordering: overfits a 6-task benchmark by construction (keyword table matches task *names*, not a validated persona-fit signal).
- Lowering attempt cap to 2 (drop Security Auditor): deterministically converts the one 3rd-attempt rescue into a failure (180/180→179/180) to save a cost that's already negligible.
- Complexity-gated roster size via description-keywords: core "savings" mechanism is already free (loop breaks on pass); unverified whether hard tasks' descriptions even match the keyword lists.
- Self-critique pre-write pass: doubles cost/latency per attempt for a benchmark already at 100% pass rate; self-critique of own code is a known-unreliable, noisier proxy for the real signal (execute_tests) the loop already gets for free.
- Streaming/partial-response salvage: no telemetry evidence truncation is even happening; the real diagnosed failure mode (malformed JSON) is already fixed by the reformat retry.
- Failure_log truncation/grep-shortening: touches only 7/180 runs, real risk of stripping diagnostic signal on non-AssertionError failures (concurrency/timeout errors) for an unmeasurable latency gain.
- Skip acceptance-tests block for trivial tasks: trims the exact field driving the pass rate for a latency win smaller than measurement noise.
- Best-of-N parallel generation / adding a 4th persona: 3x cost tax for zero headroom (already 180/180); explicitly recommended against, adopted as-is.
- Production ASG entrypoint reusing run_asg_mode's loop as-is: premise unverified (nudger.py's real path not confirmed to lack this), and real repos often lack acceptance tests, degrading the exact signal the mechanism depends on — needs an investigation spike first, not a build.
- AST-complexity gate repurposed as ASG-mode on/off switch: wrong proxy (validated for model-tier routing, not "will retry help"), a cheap diff-size heuristic gets the same result with no re-validation burden.
- CLI/PR-comment surfacing of "attempts": the `attempts` field is benchmark-only: no production caller computes it today, so this is mislabeled as plumbing when it's actually new logic.
- Log roster choice into GraphMemoryEngine for future dynamic rosters: roster is a hardcoded constant on every ASG run today — logging a constant is provably useless data collection.

## 3. Phased plan (BUILD / RECONSIDER-with-modification only)

### Phase 1 — Low-risk pipeline + prompt improvements (no shared-schema changes, benchmark-safe)

**1.1 Pre-sandbox syntax gate**
- Mechanism: in `gemini_adapter.py`, after `_write_files_from_json` writes files (and after the reformat retry), loop written files through `compile(source, path, 'exec')`. On `SyntaxError`, format `filename:lineno:offending-line-text` into `failure_log` and skip `execute_tests` for that attempt — same pattern as the existing "zero files written" failure path.
- Files: `src/self_governance/gemini_adapter.py`
- Cost: S
- What would have to be TRUE to matter: a nonzero rate of syntactically-invalid-but-JSON-valid generations exists in real traffic (current telemetry shows ~0 evidence of this — it's a latency/cost micro-optimization on a rare tail, not a pass-rate lever). Must confirm it doesn't change baseline's pass/fail semantics (baseline hits the same code path but has no retry, so a caught syntax error there just fails as before — verify this explicitly).
- Verification: unit test asserting compile() gate fires on a deliberately broken written file and produces a lineno-bearing failure_log string; confirm baseline behavior unchanged via a targeted rerun.

**1.2 Acceptance tests as a fenced prompt block, not buried in JSON**
- Mechanism: in `gemini_adapter.py::execute_development` (~line 523), pull `plan.get('acceptance_tests')` out of the `json.dumps(plan)` blob and print it separately as a fenced "The following tests MUST pass" block. Baseline's plan never sets `acceptance_tests`, so this is a no-op for baseline by construction (not by discipline) — verify that explicitly rather than assuming it.
- Files: `src/self_governance/gemini_adapter.py`
- Cost: S
- What would have to be TRUE to matter: models are currently skimming past test code embedded in escaped JSON. No A/B evidence either way — this must be validated, not assumed, before being called an improvement.
- Verification: **mandatory** — rerun the existing 30-rep sweep (`telemetry/analyze_sweep.py`) before/after on identical model/config. Keep only if pass rate ≥ 180/180 and latency/cost don't regress. Revert via git immediately if either regresses. No feature flag — it's cheap enough to just try and revert.

Both 1.1 and 1.2 are additive to a shared file used by both modes — that's the actual risk in this phase, not the diffs themselves. Treat "did baseline's number move" as the tripwire for both.

### Phase 2 — Wiring existing unused modules into the *production* path (nudger.py), explicitly NOT the benchmark

**2.1 Wire `GraphMemoryEngine.query_context` into the real plan before generation**
- Mechanism: in `nudger.py`, add `graph_engine.query_context(current_features)` output as `plan['project_context']` in the real (webhook) call path, so cross-session constraints/conventions reach the prompt. `GraphMemoryEngine` already exists and is already instantiated in nudger.py (line 915) — this reuses it, doesn't build new schema.
- Files: `src/self_governance/nudger.py`, `src/self_governance/graph_memory.py` (read-only call, no schema change)
- Cost: S, **contingent** on a pre-check: grep nudger.py to confirm whether `current_features: List[str]` is already computed at the plan-construction site. If it must be newly derived, this is M, not S — check before committing to S.
- What would have to be TRUE to matter: (a) enough prior sessions exist per tenant for `query_context` to return non-empty results (today it likely returns "No specific past graph context found" most of the time — this is a cold-start problem, not a bug); (b) the string-overlap feature matching in `query_context` produces relevant, not noisy, hits on real feature names.
- Cannot regress benchmark: benchmark.py never constructs or calls `GraphMemoryEngine` — confirmed by grep across the codebase. Zero blast radius to the 180/180 number.
- Note the honest limit: this is unvalidated by any existing measurement (no production eval harness exists yet). Ship it as an inert improvement, not a claimed win, until a production-side eval exists.
- Verification: add a small script/self-check that calls `query_context` against a seeded 2-session fixture and asserts a non-empty, feature-matched constraint string comes back — this is the "runnable check" that fails if the wiring breaks, since there's no benchmark to catch it.

### Phase 3 — Benchmark rigor expansion (only after Phase 1/2 land and are stable)

**3.1 Concentrated rep increase on the 3 tasks that actually show variance**
- Mechanism: rerun `run_benchmark_parallel` with reps≈100/mode on just `task_lru_cache`, `task_retry_backoff`, `task_thread_safe_cache` (not all 6 — the other 3 are ceiling-bound at 30/30 both modes and add cost without adding information). Reuse `analyze_sweep.py` unmodified.
- Files: none (invocation-only), output to a new `telemetry/phase_g_*.jsonl`, update `docs/BENCHMARKING.md` with the new CI.
- Cost: **M**, not S as originally framed — this is real API spend (~100 reps × 2 modes × 3 tasks × up to 3 ASG model calls each), not free.
- What would have to be TRUE to matter: the true effect size is large enough that ~100 reps/task narrows baseline's upper CI bound below ASG's lower bound. If the true underlying pass rates are close (e.g. baseline 93%, ASG 97%), even 100 reps may still show overlap — this is a real possibility the plan must accept, not paper over.
- This is the single most important item for answering "is +2.2pp real" — do it before adding new tasks, since new tasks introduce a second confound (task selection) on top of the CI-width question.

**3.2 Held-out task tier (same schema, no benchmark.py changes)**
- Mechanism: append 2-4 new tasks to `benchmark_tasks.json` (or a separate `benchmark_tasks_heldout.json` if isolation is wanted) using the **existing** single-target-file/single-test_code schema, but with richer multi-function targets and 5-8 acceptance tests each — same shape as the existing `lru_cache`/`thread_safe_cache` tasks that already produce all the observed signal. Explicitly **not** the multi-file/seed-buggy-code format (that needs new schema fields — `seed_files` — and is out of scope this iteration).
- Files: `benchmark_tasks.json` (or new heldout file), no `benchmark.py` code changes required (verify the task-loading path isn't hardcoded to a single filename before assuming this).
- Cost: M
- What would have to be TRUE to matter: someone other than whoever iterates on ASG's roster/retry mechanism designs these tasks, or the result only demonstrates instance-overfitting resistance, not mechanism-overfitting resistance — state this limitation explicitly in any report.
- Verification: `analyze_sweep.py`'s existing per-task breakdown already supports slicing old-6 vs new-N — confirm this before adding new tooling scope.

**Explicitly deferred, not built this iteration** (recorded per this repo's spec convention, not silently dropped):
- True SWE-bench-style multi-file/seed-buggy-code tasks — needs a `seed_files` schema addition and pre-generation-content plumbing in benchmark.py. Real work, do only after 3.1/3.2 show the current suite is exhausted.
- Non-Python (TS/Go) task tier — needs a second test-execution/failure-parsing backend in `gemini_adapter.py::execute_tests`, touches the shared `_run_one_isolated` dispatch used by every existing rep. High regression risk to the credible 176/180 baseline number if done carelessly. Do last, and only with a full re-run of the existing Python sweep after any dispatch-path change.
- Symmetric, task-conditioned model tiering (both modes, harness-config-level, not ASG-only/attempt-1-only): plausible cost/latency lever, but must be applied identically to baseline and ASG or it invalidates the pass-rate comparison. Needs its own validation sweep. Not scoped this iteration.
- Investigation spike into nudger.py's actual current generation path (to determine if a production roster/retry loop already exists there) — prerequisite for ever porting run_asg_mode's mechanism to production. Not started.

## 4. Fairness invariants (what made 180/180 vs 176/180 credible — restated, and how each Phase item respects them)

1. **Every attempt's cost/latency is counted in full — no free retries.** `run_asg_mode` accumulates latency/cost across every roster iteration, not just the winning one.
   - 1.1/1.2: no change to attempt accounting, pure prompt/gate content.
   - 2.1: doesn't touch benchmark.py at all — no attempts field exists in production, nothing to violate.
   - 3.1/3.2: pure measurement, doesn't touch mode functions.
2. **Baseline stays untouched / identical code path across comparisons.** Both modes share `GeminiExecutionAdapter` construction and `execute_development`/`execute_tests` — any shared-file edit risks silently changing baseline too.
   - 1.1/1.2 are the only items touching the shared `gemini_adapter.py`. Both must be verified not to fire (or to fire identically) on baseline's plan shape (`plan['acceptance_tests']` absent for baseline). This is a required check, not an assumption.
3. **Isolated execution per (task, mode, rep) — no cross-run state leakage.** `_run_one_isolated` chdir's into a fresh tempdir per unit specifically to avoid races.
   - 2.1 introduces genuine cross-session state (that's its purpose) but is confined to nudger.py, which is never called by the benchmark harness — confirmed by grep. No isolation invariant is at risk because the benchmark never touches it.
   - No Phase 1 or Phase 3 item introduces cross-run state.
4. **Claims require a fresh validation sweep, not assumption.** The design doc's own history (two prior sweeps invalidated by infra bugs) is the reason this rule exists.
   - 1.2 explicitly requires a before/after 30-rep sweep before being kept.
   - 3.1 exists specifically to re-test the current headline claim with more power.
   - Nothing in this plan should be described as "shipped and proven" without a rerun.

## 5. Production usability vs. benchmark score — where these diverge

- The benchmark's `run_asg_mode`/`run_baseline_mode` are **not called from any production path** — they exist only for `run_benchmark`/`run_benchmark_parallel`. There is currently no evidence (confirmed by grep) that production PR generation uses the roster-retry loop, the `attempts` field, or `task['test_code']`-driven repair at all. Production's actual mechanism, per the design doc, is `ConsensusEngine`/TETD for dynamic rosters via the webhook path.
- Real repos frequently **lack pre-written acceptance tests** for the touched code — the entire measured 180/180 win depends on `test_res.get("status")=="completed"` as a clean pass/fail signal from author-supplied tests. This signal may be sparse or absent in production, which would degrade or eliminate the repair loop's value outside the benchmark. This is not solved by anything in this plan and should not be implied as solved.
- Phase 2 (`GraphMemoryEngine` wiring) is the only item in this plan that touches the actual production path (`nudger.py`) — everything else is benchmark-only. **The benchmark's 180/180 number says nothing about production quality** until either (a) a production-path evaluation harness exists, or (b) the deferred investigation spike into nudger.py's real generation path is done and the benchmark's mechanism is deliberately, validatedly ported over (not assumed to transfer).
- Recommendation: do not present Phase 1/3 results as evidence of production improvement. They are benchmark-only findings about a harness function with no confirmed production caller.

## 6. Success criteria for the next validation sweep

- **Trigger for "the effect is real"**: after Phase 3.1's concentrated rep increase on the 3 variance-bearing tasks, baseline's upper 95% CI bound must fall below ASG's lower 95% CI bound. Simple pass-rate delta without CI separation is not sufficient given this project's history of small-n false positives.
- **Trigger for "Phase 1 changes are net positive"**: post-change 30-rep sweep (same 6 tasks, same config) must show ASG pass rate ≥ 180/180 and baseline pass rate unchanged (176/180 ± sampling noise) and mean latency/cost within measurement noise of current 20.8s/21.1s. Any baseline regression is an automatic revert, no exceptions — a baseline regression means a shared-file change leaked into baseline's path, which is exactly the failure mode Section 4 exists to catch.
- **Trigger for "held-out tier is informative"**: pass-rate delta (ASG − baseline) on the new tasks must be reported separately from, not blended into, the original 6-task number, with an explicit note on whether task designers had visibility into ASG's roster mechanism (instance-overfitting caveat).
- **Minimum bar to write anything up publicly**: cite exact n, exact CIs, exact task-level breakdown, and explicitly state whether CIs overlap — matching the discipline already used in the evidence base above. No aggregate percentage without its interval.
