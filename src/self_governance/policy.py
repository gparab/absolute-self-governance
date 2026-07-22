"""Centralized, audited policy gate for ASG's own dangerous actions (Phase D1).

Every subprocess/git call the Ship Phase makes today is allowed by a
`# nosec B603 B607` comment at the call site -- a human asserted "this is
fine" once, with no runtime check, no audit trail, and no test coverage of
the assertion itself. This module replaces that with a rule-based,
priority-ordered gate: every dangerous action is evaluated against a set of
PolicyRules before it runs, and every decision is auditable.

Scoped to what ASG actually needs (git/subprocess actions in the Ship
Phase), not a general-purpose sandboxing framework. See
docs/superpowers/specs/2026-07-17-automaton-inspired-hardening-plan.md
(Phase D1) for the design rationale, adapted from
Conway-Research/automaton's policy engine.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Protocol


class RiskLevel(Enum):
    """How dangerous an action is, independent of whether it's allowed."""

    SAFE = "safe"
    CAUTION = "caution"
    DANGEROUS = "dangerous"
    FORBIDDEN = "forbidden"


class ActionSource(Enum):
    """Where a policy-gated action originated. Mirrors
    injection_defense.TrustLevel but scoped to action provenance rather
    than text content: NUDGER is ASG's own Ship Phase code path, which is
    the only source trusted to perform mutating git operations."""

    NUDGER = "nudger"
    GODS_EYE = "gods_eye"
    EXTERNAL = "external"


class Decision(Enum):
    ALLOW = "allow"
    DENY = "deny"


@dataclass
class AgentBudget:
    """Resource-bounded delegation budget (Agent Contracts, research.google
    survey, July 2026 topic-page batch): a parent's remaining budget is
    deducted, not copied, when it delegates to a sub-agent, so a child can
    never spend more than the slice it was actually handed -- a
    conservation law enforced by BudgetConservationRule, not by convention.
    """

    max_actions: int
    spent: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.max_actions - self.spent)

    def child_budget(self, allotment: int) -> "AgentBudget":
        """Carves out a sub-budget for a delegated sub-agent. Raises if the
        requested allotment exceeds what's left -- a parent cannot hand out
        more than it has."""
        if allotment > self.remaining:
            raise ValueError(
                f"cannot delegate {allotment} actions: only {self.remaining} remain"
            )
        return AgentBudget(max_actions=allotment)


@dataclass
class PolicyAction:
    """A single dangerous action awaiting a policy decision."""

    name: str
    argv: List[str] = field(default_factory=list)
    path: Optional[str] = None
    content: Optional[str] = None
    source: ActionSource = ActionSource.NUDGER
    risk_level: RiskLevel = RiskLevel.CAUTION
    budget: Optional[AgentBudget] = None


@dataclass
class PolicyDecision:
    decision: Decision
    rule_name: str
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision == Decision.ALLOW


class PolicyRule(Protocol):
    """A single evaluable policy rule.

    evaluate() returns None to abstain (fall through to the next rule, or
    to the default allow if no rule has an opinion), or a PolicyDecision to
    settle the question. Rules are evaluated in priority order (lower
    first); the first non-None DENY wins and short-circuits evaluation.
    """

    name: str
    priority: int

    def evaluate(self, action: PolicyAction) -> Optional[PolicyDecision]: ...


_DEFAULT_ALLOW = PolicyDecision(
    decision=Decision.ALLOW, rule_name="default", reason="No rule denied this action."
)


class PolicyEngine:
    """Evaluates PolicyActions against an ordered set of PolicyRules."""

    def __init__(self, rules: List[PolicyRule]):
        self.rules = sorted(rules, key=lambda r: r.priority)

    def check(self, action: PolicyAction) -> PolicyDecision:
        for rule in self.rules:
            result = rule.evaluate(action)
            if result is not None and result.decision == Decision.DENY:
                return result
        return _DEFAULT_ALLOW


@dataclass(frozen=True)
class TaskPolicy:
    """A verified, task-scoped permission set (VeriGuard, research.google
    survey, July 2026 topic-page batch, Tier 2): VeriGuard synthesizes a
    minimal per-task policy from a task's declared needs and formally
    verifies it before granting, rather than trusting a single global
    rule set to be right for every task shape.

    Scoped down to what ASG can actually verify without a theorem prover:
    synthesize_task_policy() checks the synthesized allow-list against a
    set of forbidden action names and refuses to produce a TaskPolicy that
    would permit any of them -- a syntactic verification, not a semantic
    one, but a real check rather than an unverified allow-list.
    """

    allowed_action_names: "frozenset[str]"

    def permits(self, action_name: str) -> bool:
        return action_name in self.allowed_action_names


class PolicySynthesisError(Exception):
    """Raised when synthesize_task_policy's requested allow-list would
    grant a forbidden action -- the synthesis itself is unsafe, not just a
    single action being denied at check-time."""


def synthesize_task_policy(
    requested_actions: List[str], forbidden_action_names: "frozenset[str]"
) -> TaskPolicy:
    """Synthesizes a verified, minimal TaskPolicy for one task.

    Not wired into PolicyEngine.check() or PolicyAction -- a caller that
    wants task-scoped permission verification builds a TaskPolicy for a
    given task (e.g. from that task's declared tool/action needs) and
    checks candidate actions against it before -- or in addition to --
    the existing rule-based PolicyEngine.

    Args:
        requested_actions: the action names this task claims it needs.
        forbidden_action_names: a global forbidden set (e.g. mutating git
            subcommands for a read-only analysis task) the synthesis must
            never grant, regardless of what was requested.

    Returns:
        A TaskPolicy whose allowed_action_names is exactly
        set(requested_actions) minus nothing -- since verification
        already guarantees no forbidden name made it into the request.

    Raises:
        PolicySynthesisError: if any requested_actions entry is also in
            forbidden_action_names -- the synthesis itself is rejected
            rather than silently dropping the offending action, so a
            caller can't mistake a partially-granted policy for a fully
            verified one.
    """
    overlap = set(requested_actions) & forbidden_action_names
    if overlap:
        raise PolicySynthesisError(
            f"cannot synthesize a task policy that grants forbidden actions: {sorted(overlap)}"
        )
    return TaskPolicy(allowed_action_names=frozenset(requested_actions))


class PolicyDenied(Exception):
    """Raised when a policy-gated action is denied. Callers that need the
    action to be fatal (e.g. the Ship Phase) should let this propagate;
    callers that want to degrade gracefully should catch it."""

    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__(f"Policy denied by rule '{decision.rule_name}': {decision.reason}")
