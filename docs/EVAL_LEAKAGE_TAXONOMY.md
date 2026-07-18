# Eval-Leakage Taxonomy

Adopted from [sovereign-skills](https://github.com/AlexZio00/sovereign-skills)
(July 2026 `agentic-workflows` topic-page batch). An eval is "leaky" when it
can pass without the thing it claims to measure actually being true --
usually because the checker and the thing being checked share more context
or authorship than they should. This project has a direct history of
catching fabricated claims in its own benchmark and memory-recall harnesses,
so this taxonomy is used as an audit checklist against ASG's own eval
surfaces (`benchmark.py`, `telemetry/eval_memory_recall.py`,
`ConsensusEngine`'s vote parsing), not just external tools.

## The 8 patterns

1. **checker_overfit** -- the checker was tuned against the exact outputs it
   now grades, so it rewards matching a known-good string rather than
   correctness in general.
2. **verifier_is_designer** -- the same actor (person or agent) both wrote
   the thing under test and wrote the test that grades it, with no
   independent check in between.
3. **shared_pool_bias** -- train/eval data (or task/verification prompts)
   are drawn from the same generation pool, so passing partly reflects
   memorized surface features of that pool.
4. **self_confirming_loop** -- the system under test also produces the
   verdict on itself (e.g. an LLM grades its own output) with no
   adversarial or independent second opinion.
5. **silent_default_pass** -- a malformed, missing, or unparseable
   result is treated as a pass (or a moderate score) instead of a
   failure, because the failure path was never actually exercised.
6. **metric_gaming** -- the target metric can be improved by an action that
   doesn't improve the underlying capability (e.g. weakening the test
   the metric is measured against).
7. **temporal_leakage** -- the eval was authored, or the model was
   trained/tuned, with knowledge of results that would only be available
   after the eval "should" have run.
8. **survivorship_reporting** -- only the runs that reached a checkpoint
   (or passed some earlier gate) are counted, silently excluding failures
   that never got that far from the denominator.

## Where each pattern is mitigated in this project

| Pattern | Mitigation | Where |
|---|---|---|
| checker_overfit | Held-out task set (`benchmark_tasks_heldout.json`) authored without visibility into ASG's mechanism specifics | `benchmark.py` |
| verifier_is_designer | Disjoint write-scope: the specialist persona authoring an attempt is structurally barred from writing to the acceptance test file it's judged against | `gemini_adapter.py`'s `protected_write_paths` |
| self_confirming_loop | Fail-closed vote parsing: an unparseable consensus vote counts as a dissent, never a silent pass | `consensus.py`'s `_parse_llm_score` |
| silent_default_pass | Same fail-closed fix as above, plus the memory-recall harness asserting an exact documented default (not "anything non-empty") for an unbuilt feature | `consensus.py`, `telemetry/eval_memory_recall.py` |
| metric_gaming | Same disjoint-write-scope mechanism as verifier_is_designer -- a generated attempt can't pass by rewriting its own test | `gemini_adapter.py` |
| survivorship_reporting | Failure taxonomy classifies infrastructure failures (revoked key, Docker down) separately from genuine test failures, instead of silently dropping them from the denominator | `benchmark.py`'s `_SANDBOX_ERROR_MARKERS` |

## Not yet mitigated

- **shared_pool_bias**: the six original benchmark tasks and the held-out
  set were both authored by the same project maintainer; a truly
  independent task pool (e.g. commissioned from someone with no visibility
  into ASG at all) would close this gap further.
- **temporal_leakage**: not directly applicable today (no model
  fine-tuning happens in this project), but would need attention if ASG
  ever trained rather than prompted a model.

This is a documentation-only audit pass, not new enforcement code -- the
mitigations above already exist; this file makes the taxonomy explicit so
future eval surfaces added to this project get checked against the same
eight patterns before being trusted.
