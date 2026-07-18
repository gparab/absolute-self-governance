# Related Projects

Honest positioning: this project is a personal/research-stage effort, not a
competitor by adoption to any of the projects below — several have tens of
thousands of stars and full teams behind them. This page exists to help
anyone landing here understand where TETD consensus and dynamic swarm
sizing sit relative to the existing landscape, and to point at libraries
that could genuinely extend this repo's own functionality.

All descriptions below are written in our own words; none of this text is
copied from the linked projects. See each project's own repository for
authoritative documentation.

## Same problem space: role-based multi-agent SDLC orchestration

- **[MetaGPT](https://github.com/FoundationAgents/MetaGPT)** — PM/architect/engineer/QA
  roles collaborating via Standard Operating Procedures and structured
  document handoffs. The closest conceptual peer to this project; unlike
  ASG's TETD voting, MetaGPT's roster is fixed rather than consensus-selected,
  and roles genuinely execute independent turns rather than a single
  roster-informed call.
- **[ChatDev](https://github.com/openbmb/ChatDev)** — CEO/CTO/programmer roles
  running a virtual software company's SDLC through free-form agent dialogue.
  Recently rewritten (2.0/DevAll) as a zero-code orchestration platform.

## Adjacent: autonomous coding agents

- **[OpenHands](https://github.com/OpenHands/OpenHands)** (formerly OpenDevin) —
  the largest open-source autonomous coding agent by adoption. Single-agent,
  cloud-hosted, sandboxed.
- **[SWE-agent](https://github.com/princeton-nlp/SWE-agent)** — Princeton's
  research-grade reference implementation for GitHub-issue-to-PR resolution
  via a structured Agent-Computer Interface.
- **[Aider](https://github.com/paul-gauthier/aider)**, **[OpenCode](https://github.com/opencode-ai/opencode)**,
  **[Goose](https://github.com/block/goose)** — general-purpose, model-agnostic
  agentic coding CLIs. Not role/consensus-based; closer to a single very
  capable pair programmer than a council.

## Libraries that could extend this repo (not currently dependencies)

- **[LiteLLM](https://github.com/BerriAI/litellm)** — unified gateway to 100+
  LLM providers with built-in cost tracking and retries. This repo currently
  hand-rolls `urllib` calls to a single provider (`gemini_adapter.py`);
  LiteLLM would let `model_default`/`model_review`/etc. address other
  providers with minimal code change.
- **[level12/pals](https://github.com/level12/pals)** — PostgreSQL advisory
  locks as a context manager. The nudger is documented as single-instance
  because coordination is a local `threading.Lock`; since this project
  already runs on Postgres, `pals` is the lowest-effort path to multi-instance
  coordination without adding new infrastructure.
- **[e2b-dev/code-interpreter](https://github.com/e2b-dev/code-interpreter)** —
  managed ephemeral sandboxes for running AI-generated code. This repo's own
  Docker-based sandbox (`gemini_adapter.py`'s `execute_tests`) is hand-maintained;
  a purpose-built, security-audited execution boundary is the more robust
  long-term choice for the same job.
- **[Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/)** —
  typed settings from env vars/secrets files. `config.py` currently hand-validates
  YAML against a hardcoded default dict; this project already depends on
  Pydantic elsewhere, so adopting `pydantic-settings` would remove a
  hand-rolled validator for a maintained one.
- **[githubkit](https://github.com/yanyongyu/githubkit)** — fully-typed,
  async GitHub SDK with built-in webhook signature verification. `github_app.py`
  currently hand-rolls HMAC verification and untyped payload parsing.

## Ideas adopted from the `agentic-workflows` GitHub topic

Surveyed the ~14 most-starred, architecturally-relevant repos tagged
[`agentic-workflows`](https://github.com/topics/agentic-workflows) for
patterns orthogonal to what's already here. Most were thin skill/prompt
packages this project has already surpassed; three ideas were genuinely
novel and got adopted directly:

- **[looper](https://github.com/ksimback/looper)** — fail-closed judge-verdict
  parsing: an unparseable LLM vote must count as a dissent, never silently
  default to a passing score. `consensus.py`'s `_parse_llm_score` previously
  defaulted unparseable output to `7.5`, above the consensus approval floor;
  it now returns `0.0` with a `PARSE_FAILURE` justification.
- **looper** (same repo) — no-progress stall detection: hash the set of
  currently-failing tests per attempt; an identical signature across
  consecutive attempts means the rewrite made no progress, distinct from
  failing differently. `benchmark.py`'s ASG mode now tracks this as
  `stalled_attempts` (observability only; doesn't change the pass/fail
  verdict).
- **[Agent-Loop-Skills](https://github.com/gaasher/Agent-Loop-Skills)** —
  structurally enforced disjoint write-scope: the agent authoring an attempt
  must be barred from writing to the file it's being verified against, so it
  can't game the gate by weakening its own test. `gemini_adapter.py` now
  accepts a `protected_write_paths` plan key that both file-writing code
  paths respect; the benchmark harness passes the acceptance test file.

## Where this project is different, for better or worse

- **TETD consensus** (temperature annealing + threshold decay) is a real,
  distinct mechanism for *selecting* a roster — neither MetaGPT's fixed SOPs
  nor ChatDev's free-form dialogue have an adaptive voting algorithm deciding
  who's on the team at all.
- **Execution is currently a single roster-informed API call per phase**,
  not parallel independent agent turns. This is a genuine capability gap
  relative to MetaGPT/ChatDev, stated plainly rather than implied otherwise —
  see [paper.pdf](paper.pdf) §3.4.
- A scaled benchmark (`telemetry/phase_c_benchmark_scaled_results.json`)
  found the roster-informed approach did *not* reliably outperform a plain
  single-call baseline at current sample size, while costing 3–4× more.
  See the paper's §4.7 for the full, unflattering result.
