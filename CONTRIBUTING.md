# Contributing

## Setup

```bash
git clone https://github.com/gparab/absolute-self-governance.git
cd absolute-self-governance
uv sync
```

## Before opening a PR

```bash
uv run pytest -q --cov=src/self_governance --cov-branch --cov-fail-under=90
uv run ruff check src tests
uv run mypy src
```

All three are enforced in CI (`.github/workflows/ci.yml`) and must pass. There
is no `type: ignore`/suppression escape hatch for new code — the two that
exist in `models.py` are narrowly scoped and individually justified in a
comment; new ones need the same bar, not a lower one.

## What this codebase expects

- **No dead code, no speculative abstractions.** If you're adding a config
  knob, an interface, or a flag nobody uses yet, don't — add it when there's
  a second real caller.
- **Comments explain *why*, not *what*.** Well-named code explains itself;
  a comment should only exist for a non-obvious constraint, a workaround, or
  a decision someone would otherwise redo incorrectly.
- **Claims need evidence.** If a change touches `paper_gen_code/absolute_self_governance_paper.md`
  or any number in `README.md`, it needs a real, re-runnable source — a test,
  a script in `telemetry/`, or a command someone can actually execute. This
  project's history includes finding and removing fabricated benchmark
  numbers; we're not adding new ones.
- **Real infrastructure over mocks, where practical.** Prefer a fix verified
  against a real Postgres/Docker/API over one only verified against the
  mock/test adapter path, when the two could plausibly diverge.

## Response time

PRs and issues get a same-day first response. If that stops being true,
something's wrong — say so.

## Good first issues

Labeled [`good first issue`](https://github.com/gparab/absolute-self-governance/labels/good%20first%20issue)
on the issue tracker. See also [RELATED_PROJECTS.md](RELATED_PROJECTS.md) for
libraries that could extend this repo's own functionality.
