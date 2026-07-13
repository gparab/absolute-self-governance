# ASG repair loop design — making the pipeline a mechanism, not ceremony

Date: 2026-07-12
Status: approved (session discussion)

## Evidence base

Two independent full sweeps agree ASG mode loses to a single-agent
baseline on the 6-task benchmark:

- Gemini 2.5 Flash (paper §4.7, n=30/mode): 26/30 vs 25/30, ASG −3.4pp.
- Nemotron-3-Ultra via OpenRouter (n=180/mode): 169/180 (93.9%) vs
  159/180 (88.3%), ASG −5.6pp, ~5.4× latency. (Direction valid;
  magnitude partly confounded — the harness shim dropped persona
  system-instructions and capped output tokens.)

Root causes confirmed by code reading and a live captured diagnostic:

1. **No feedback loop.** `review_code`/`run_security_scan` outputs are
   discarded; `dimension_swarm` result assigned to `_`; a test failure
   is terminal. ASG's extra stages cannot change the outcome.
2. **Malformed generation = silent guaranteed failure.** A captured
   run showed ASG writing zero files (JSON parse failure → legacy
   parser found nothing → tests ran on an empty dir). No retry, no
   repair, no distinction from "wrote bad code."
3. **Task-blind deliberation.** Consensus scores generic "capability
   alignment"; the task never conditions the vote.

## Changes in scope (this iteration)

- **R1 — Test-driven repair loop** in `run_asg_mode`: on test failure,
  feed the pytest output back and regenerate, max 2 repair rounds.
  Baseline stays single-shot by definition — the loop IS ASG's
  differentiator. Each round re-runs the sandboxed tests; first pass
  wins; loop exits early on pass.
- **R2 — Structured-output robustness** in `execute_development`: when
  both the JSON parser and the legacy parser produce zero written
  files from a non-empty response, make one reformat call ("return the
  same content as valid JSON per the schema") before giving up.
  Applies to both modes (it's a correctness fix, not an ASG feature).
- **R3 — QA persona sees the tests**: `run_asg_mode` includes the
  task's test code in the generation plan. That is what a "QA
  Specialist" perspective should mean; baseline continues to see only
  the task description.

## Deferred (recorded, not built now)

- Best-of-N candidate generation with test-based selection.
- Complexity-gating the ASG path (the AST gate exists but keys off
  generated-file AST, which doesn't exist pre-generation; needs a
  description-based proxy first).
- Task-conditioned, batched consensus scoring.
- Stage-appropriate model routing beyond what config already allows.

## Honest-measurement rules

- Repair rounds count their full latency and token cost in the unit's
  metrics — no free retries.
- The benchmark reports repair-round usage (how many units needed 1 or
  2 rounds) so the paper can state exactly where the wins came from.
- Rerun the full sweep after implementation; report whichever way it
  lands, including if the repair loop does not close the gap.

## Success criterion

ASG aggregate pass rate ≥ baseline on a fresh 30-rep sweep, with the
cost/latency multiple reported alongside. If it still loses, the paper
records that too.
