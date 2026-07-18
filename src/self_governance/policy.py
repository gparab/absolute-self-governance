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
class PolicyAction:
    """A single dangerous action awaiting a policy decision."""

    name: str
    argv: List[str] = field(default_factory=list)
    path: Optional[str] = None
    content: Optional[str] = None
    source: ActionSource = ActionSource.NUDGER
    risk_level: RiskLevel = RiskLevel.CAUTION


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


class PolicyDenied(Exception):
    """Raised when a policy-gated action is denied. Callers that need the
    action to be fatal (e.g. the Ship Phase) should let this propagate;
    callers that want to degrade gracefully should catch it."""

    def __init__(self, decision: PolicyDecision):
        self.decision = decision
        super().__init__(f"Policy denied by rule '{decision.rule_name}': {decision.reason}")
