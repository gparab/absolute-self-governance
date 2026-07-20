# Running the benchmark

`self-governance benchmark` compares single-agent baseline generation
against the full ASG pipeline (TETD consensus, swarm dimensioning,
roster-informed generation, review, security scan) on the same tasks,
verified identically in a sandboxed pytest run.

```bash
# Sequential, 1 rep/task/mode (quick smoke check)
self-governance benchmark

# Concurrent, process-isolated, resumable
self-governance benchmark --reps 30 --workers 8 --out checkpoint.jsonl
```

## Choosing a model

Nothing in the benchmark path is hardcoded to a specific model or vendor:

- `--model <name>` overrides the model for every stage (development,
  review, security, and the baseline call) for the whole sweep. Baseline
  and ASG modes always run against the same model within one sweep --
  that's what makes the comparison meaningful.
- Without `--model`, the adapter falls back to `config.yaml`'s `models:`
  section, then to `ASG_DEFAULT_MODEL` (env var), then to a built-in
  default.
- `ASG_MODEL_TIER_1/2/3` (env vars) control the adaptive AST-complexity
  router used elsewhere in the pipeline (see `economics.py`) when no
  explicit model is given to a call.

```bash
export ASG_DEFAULT_MODEL=gemini-2.5-flash   # or any model your adapter/provider supports
self-governance benchmark --reps 30 --workers 8 --model "$ASG_DEFAULT_MODEL" --out checkpoint.jsonl
```

The wire protocol itself (Gemini's REST format) is still what
`gemini_adapter.py` speaks by default; `providers.py` is the extension
point for other protocols (see its `LLMProvider` interface) if you want
to point the benchmark at a different backend entirely.

## Width vs. depth: the recursive-refinement ablation

The default ASG arm rotates through 3 *distinct* specialist personas
(Backend Wizard, QA Specialist, Security Auditor), one attempt each. This
is a "width" strategy: more, different perspectives, one try apiece.

"Less is More: Recursive Reasoning with Tiny Networks" (Jolicoeur-Martineau,
2025, arXiv:2510.04871) found the opposite works better for tiny recursive
neural networks solving hard puzzle tasks: a single small network
recursively refining its own answer beat two specialized networks working
at different timescales, at equal computational depth. Whether the same
holds for LLM agent personas on *this* benchmark suite is an open,
testable question, not an assumption — `--include-recursive-ablation`
adds a third arm at the same 3-attempt budget, but with a single persona
(`persona_strategy="recursive"` on `run_asg_mode`) refining its own prior
attempt instead of rotating:

```bash
self-governance benchmark --include-recursive-ablation
```

This triples the ASG-side LLM spend for the sweep (rotate + recursive,
each 3 attempts) — off by default. Only supported on the sequential path
(`--reps 1`); not yet wired into `run_benchmark_parallel`'s reps/workers
sweep.

## Resuming a checkpointed sweep

`--out checkpoint.jsonl` makes a sweep resumable: each completed
`(task, mode, rep)` is appended as it finishes, and a later run with the
same `--out` path skips anything already recorded there. Runs that ended
in an error (e.g. a quota failure) are **not** treated as done and will
be retried on the next invocation.

**Do not point two different `--model` values at the same checkpoint
file.** A checkpoint is only a valid comparison if every recorded unit
ran against the same model; use a separate `--out` file per model.

## A real incident, for anyone reproducing this

During development, a 30-rep/6-task sweep was invalidated mid-run when
the API key was revoked between sessions -- confirmed independently via
a direct request to the provider's API with no code involved. Every call
failed at the authentication layer. The ASG-mode units in that run each
took ~185 seconds to fail, not because of a bug, but because the
consensus roster-voting loop correctly ran to its documented 1000-iteration
safety cap (see the paper, §5.2) when every vote came back unscored. If
you see uniform, suspiciously-round-number latencies across every ASG
unit and near-zero pass rates in both modes, check your credentials
before concluding anything about model quality.
