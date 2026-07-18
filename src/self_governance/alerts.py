"""Small alerting layer over ASG's existing telemetry (Phase D4).

ASG has OTel spans (Phase B3) and analysis scripts (telemetry/analyze_sweep.py,
telemetry/check_regression.py, telemetry/eval_memory_recall.py), but nothing
proactive -- a regression or a consecutive-failure streak is only caught if
someone remembers to run analysis. This adds a small, cooldown-gated rule
engine over data these subsystems already produce; it invents no new metrics
collection.

Rules operate on a plain context dict rather than a single unified event
schema -- nudger.py's NDJSON event stream, the memory-recall harness's check
results, and (potentially) future producers all have different shapes, and
forcing them through one schema would be more machinery than three small
producers need. Each rule reads only the context keys it cares about and
abstains (returns None) if they're absent.
"""

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Protocol


@dataclass
class Alert:
    rule_name: str
    message: str


class AlertRule(Protocol):
    name: str
    cooldown_seconds: float

    def evaluate(self, context: Dict[str, Any]) -> Optional[str]: ...


@dataclass
class ConsecutiveFailureRule:
    """Fires once a named streak counter in the context reaches a threshold.

    Generalizes the benchmark harness's sandbox_error circuit breaker
    (Phase A) to any producer that tracks "N in a row with no success in
    between" as a plain integer in its context.
    """

    name: str
    context_key: str
    threshold: int
    cooldown_seconds: float = 300.0

    def evaluate(self, context: Dict[str, Any]) -> Optional[str]:
        count = context.get(self.context_key)
        if count is None or count < self.threshold:
            return None
        return f"{self.context_key} reached {count} consecutive occurrences (threshold {self.threshold})"


@dataclass
class RateThresholdRule:
    """Fires when numerator/denominator clears a rate threshold, ignoring
    small samples (denominator below min_denominator) to avoid noise from
    a handful of early calls."""

    name: str
    numerator_key: str
    denominator_key: str
    threshold: float
    min_denominator: int = 5
    cooldown_seconds: float = 300.0

    def evaluate(self, context: Dict[str, Any]) -> Optional[str]:
        numerator = context.get(self.numerator_key)
        denominator = context.get(self.denominator_key)
        if numerator is None or denominator is None or denominator < self.min_denominator:
            return None
        rate = numerator / denominator
        if rate < self.threshold:
            return None
        return (
            f"{self.numerator_key}/{self.denominator_key} rate {rate:.2f} "
            f"(>= threshold {self.threshold}) over {denominator} samples"
        )


@dataclass
class HarnessRegressionRule:
    """Fires when a context key holding a list of failed check names is
    non-empty -- used for the memory-recall harness's 9 checks going from
    green to red."""

    name: str
    context_key: str
    cooldown_seconds: float = 300.0

    def evaluate(self, context: Dict[str, Any]) -> Optional[str]:
        failed = context.get(self.context_key)
        if not failed:
            return None
        return f"{len(failed)} check(s) failed: {', '.join(failed)}"


class AlertEngine:
    """Evaluates a context dict against a set of cooldown-gated AlertRules."""

    def __init__(self, rules: List[AlertRule], now: Optional[Callable[[], float]] = None):
        self.rules = rules
        self._now = now or time.time
        self._last_fired: Dict[str, float] = {}

    def check(self, context: Dict[str, Any]) -> List[Alert]:
        fired = []
        now = self._now()
        for rule in self.rules:
            last = self._last_fired.get(rule.name)
            if last is not None and (now - last) < rule.cooldown_seconds:
                continue
            message = rule.evaluate(context)
            if message:
                fired.append(Alert(rule_name=rule.name, message=message))
                self._last_fired[rule.name] = now
        return fired


def default_alert_rules() -> List[AlertRule]:
    """The 3 rules from the D4 design: consecutive verify-phase failures in
    the nudger's own event stream, a policy-deny rate spike (Phase D1), and
    a memory-recall harness regression (Phase C2a)."""
    return [
        ConsecutiveFailureRule(
            name="consecutive_verify_failures", context_key="consecutive_verify_failed", threshold=3
        ),
        RateThresholdRule(
            name="policy_deny_rate_spike",
            numerator_key="policy_denied_count",
            denominator_key="policy_checked_count",
            threshold=0.5,
        ),
        HarnessRegressionRule(name="memory_recall_regression", context_key="memory_recall_failed_checks"),
    ]
