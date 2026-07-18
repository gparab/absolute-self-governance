from self_governance.alerts import (
    AlertEngine,
    ConsecutiveFailureRule,
    HarnessRegressionRule,
    RateThresholdRule,
    default_alert_rules,
)


def test_consecutive_failure_rule_abstains_below_threshold():
    rule = ConsecutiveFailureRule(name="x", context_key="streak", threshold=3)
    assert rule.evaluate({"streak": 2}) is None


def test_consecutive_failure_rule_fires_at_threshold():
    rule = ConsecutiveFailureRule(name="x", context_key="streak", threshold=3)
    message = rule.evaluate({"streak": 3})
    assert message is not None
    assert "3" in message


def test_consecutive_failure_rule_abstains_when_key_missing():
    rule = ConsecutiveFailureRule(name="x", context_key="streak", threshold=3)
    assert rule.evaluate({}) is None


def test_rate_threshold_rule_abstains_below_min_denominator():
    rule = RateThresholdRule(name="x", numerator_key="n", denominator_key="d", threshold=0.5, min_denominator=5)
    assert rule.evaluate({"n": 4, "d": 4}) is None


def test_rate_threshold_rule_fires_above_threshold():
    rule = RateThresholdRule(name="x", numerator_key="n", denominator_key="d", threshold=0.5, min_denominator=5)
    message = rule.evaluate({"n": 5, "d": 6})
    assert message is not None
    assert "0.83" in message


def test_rate_threshold_rule_abstains_below_threshold():
    rule = RateThresholdRule(name="x", numerator_key="n", denominator_key="d", threshold=0.5, min_denominator=5)
    assert rule.evaluate({"n": 1, "d": 10}) is None


def test_rate_threshold_rule_abstains_when_keys_missing():
    rule = RateThresholdRule(name="x", numerator_key="n", denominator_key="d", threshold=0.5)
    assert rule.evaluate({}) is None
    assert rule.evaluate({"n": 5}) is None


def test_harness_regression_rule_fires_on_nonempty_list():
    rule = HarnessRegressionRule(name="x", context_key="failed")
    message = rule.evaluate({"failed": ["check_a", "check_b"]})
    assert message is not None
    assert "check_a" in message
    assert "2" in message


def test_harness_regression_rule_abstains_on_empty_list():
    rule = HarnessRegressionRule(name="x", context_key="failed")
    assert rule.evaluate({"failed": []}) is None


def test_harness_regression_rule_abstains_when_key_missing():
    rule = HarnessRegressionRule(name="x", context_key="failed")
    assert rule.evaluate({}) is None


def test_alert_engine_fires_matching_rules_only():
    engine = AlertEngine(rules=[ConsecutiveFailureRule(name="streak_alert", context_key="streak", threshold=3)])

    fired = engine.check({"streak": 5})

    assert len(fired) == 1
    assert fired[0].rule_name == "streak_alert"


def test_alert_engine_respects_cooldown():
    clock = {"t": 0.0}
    rule = ConsecutiveFailureRule(name="streak_alert", context_key="streak", threshold=1, cooldown_seconds=10.0)
    engine = AlertEngine(rules=[rule], now=lambda: clock["t"])

    first = engine.check({"streak": 5})
    assert len(first) == 1

    clock["t"] = 5.0  # still within cooldown
    second = engine.check({"streak": 5})
    assert second == []

    clock["t"] = 11.0  # cooldown elapsed
    third = engine.check({"streak": 5})
    assert len(third) == 1


def test_alert_engine_continues_past_an_abstaining_rule_to_a_firing_one():
    engine = AlertEngine(rules=[
        ConsecutiveFailureRule(name="quiet_rule", context_key="streak", threshold=100),
        ConsecutiveFailureRule(name="loud_rule", context_key="streak", threshold=1),
    ])

    fired = engine.check({"streak": 5})

    assert {a.rule_name for a in fired} == {"loud_rule"}


def test_alert_engine_no_rules_fires_nothing():
    engine = AlertEngine(rules=[])
    assert engine.check({"anything": 1}) == []


def test_default_alert_rules_covers_the_three_designed_rules():
    rules = default_alert_rules()
    names = {r.name for r in rules}
    assert names == {"consecutive_verify_failures", "policy_deny_rate_spike", "memory_recall_regression"}


def test_default_alert_rules_end_to_end_all_three_fire():
    engine = AlertEngine(rules=default_alert_rules())

    fired = engine.check({
        "consecutive_verify_failed": 3,
        "policy_denied_count": 5,
        "policy_checked_count": 6,
        "memory_recall_failed_checks": ["recall_check"],
    })

    assert {a.rule_name for a in fired} == {
        "consecutive_verify_failures", "policy_deny_rate_spike", "memory_recall_regression",
    }
