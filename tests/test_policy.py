from self_governance.policy import (
    ActionSource,
    Decision,
    PolicyAction,
    PolicyDenied,
    PolicyEngine,
    RiskLevel,
)
from self_governance.policy_rules import default_rule_set
from self_governance.policy_rules.authority import AuthorityRule
from self_governance.policy_rules.command_safety import (
    AutomationSourceGuardRule,
    ForbiddenCommandRule,
    ProtectedBranchDeletionRule,
)
from self_governance.policy_rules.path_protection import (
    ProtectedFileWriteRule,
    WorktreePathTraversalRule,
)
from self_governance.policy_rules.rate_limits import GitMutationRateLimitRule
from self_governance.policy_rules.import_boundary import ImportBoundaryRule, LayerRule


def test_default_allow_with_no_rules():
    engine = PolicyEngine(rules=[])
    decision = engine.check(PolicyAction(name="noop"))

    assert decision.allowed is True
    assert decision.rule_name == "default"


def test_default_rule_set_allows_benign_ship_phase_action():
    engine = PolicyEngine(rules=default_rule_set())
    action = PolicyAction(
        name="git_add", argv=["git", "add", "."], source=ActionSource.NUDGER, risk_level=RiskLevel.CAUTION
    )

    assert engine.check(action).allowed is True


def test_forbidden_command_rule_denies_force_push():
    rule = ForbiddenCommandRule()
    action = PolicyAction(name="git_push", argv=["git", "push", "--force", "origin", "master"])

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_forbidden_command_rule_abstains_on_empty_argv():
    rule = ForbiddenCommandRule()
    assert rule.evaluate(PolicyAction(name="noop", argv=[])) is None


def test_automation_source_guard_abstains_on_non_git_argv():
    rule = AutomationSourceGuardRule()
    assert rule.evaluate(PolicyAction(name="pytest", argv=["uv", "run", "pytest"], source=ActionSource.EXTERNAL)) is None


def test_worktree_path_traversal_rule_abstains_without_path():
    rule = WorktreePathTraversalRule()
    action = PolicyAction(name="git_worktree_add", argv=["git", "worktree", "add", "-b", "active_task"])
    assert rule.evaluate(action) is None


def test_git_mutation_rate_limit_abstains_on_empty_argv():
    rule = GitMutationRateLimitRule(max_mutations=1)
    assert rule.evaluate(PolicyAction(name="noop", argv=[])) is None


def test_forbidden_command_rule_allows_normal_push():
    rule = ForbiddenCommandRule()
    action = PolicyAction(name="git_push", argv=["git", "push", "origin", "active_task"])

    assert rule.evaluate(action) is None


def test_protected_branch_deletion_rule_denies_master_deletion():
    rule = ProtectedBranchDeletionRule()
    action = PolicyAction(name="git_branch", argv=["git", "branch", "-D", "master"])

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_protected_branch_deletion_rule_allows_scratch_branch_deletion():
    rule = ProtectedBranchDeletionRule()
    action = PolicyAction(name="git_branch", argv=["git", "branch", "-D", "active_task"])

    assert rule.evaluate(action) is None


def test_automation_source_guard_denies_external_merge():
    rule = AutomationSourceGuardRule()
    action = PolicyAction(name="git_merge", argv=["git", "merge", "active_task"], source=ActionSource.EXTERNAL)

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_automation_source_guard_allows_nudger_merge():
    rule = AutomationSourceGuardRule()
    action = PolicyAction(name="git_merge", argv=["git", "merge", "active_task"], source=ActionSource.NUDGER)

    assert rule.evaluate(action) is None


def test_automation_source_guard_allows_external_readonly_git():
    rule = AutomationSourceGuardRule()
    action = PolicyAction(name="git_status", argv=["git", "status"], source=ActionSource.EXTERNAL)

    assert rule.evaluate(action) is None


def test_worktree_path_traversal_rule_denies_escape():
    rule = WorktreePathTraversalRule()
    action = PolicyAction(
        name="git_worktree_add",
        argv=["git", "worktree", "add", "-b", "active_task", "../../etc/evil"],
        path="../../etc/evil",
    )

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_worktree_path_traversal_rule_allows_normal_path():
    rule = WorktreePathTraversalRule()
    action = PolicyAction(
        name="git_worktree_add",
        argv=["git", "worktree", "add", "-b", "active_task", ".planning/worktrees/active_task"],
        path=".planning/worktrees/active_task",
    )

    assert rule.evaluate(action) is None


def test_protected_file_write_rule_denies_external_handoff_write():
    rule = ProtectedFileWriteRule()
    action = PolicyAction(
        name="write_file", path="/repo/.planning/CURRENT_STATE.md", source=ActionSource.EXTERNAL
    )

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_protected_file_write_rule_allows_nudger_handoff_write():
    rule = ProtectedFileWriteRule()
    action = PolicyAction(
        name="write_file", path="/repo/.planning/CURRENT_STATE.md", source=ActionSource.NUDGER
    )

    assert rule.evaluate(action) is None


def test_authority_rule_denies_dangerous_external_action():
    rule = AuthorityRule()
    action = PolicyAction(
        name="git_reset", source=ActionSource.EXTERNAL, risk_level=RiskLevel.DANGEROUS
    )

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_authority_rule_allows_caution_external_action():
    rule = AuthorityRule()
    action = PolicyAction(name="git_status", source=ActionSource.EXTERNAL, risk_level=RiskLevel.CAUTION)

    assert rule.evaluate(action) is None


def test_authority_rule_allows_dangerous_nudger_action():
    rule = AuthorityRule()
    action = PolicyAction(name="git_merge", source=ActionSource.NUDGER, risk_level=RiskLevel.DANGEROUS)

    assert rule.evaluate(action) is None


def test_git_mutation_rate_limit_denies_after_ceiling():
    rule = GitMutationRateLimitRule(max_mutations=2)
    action = PolicyAction(name="git_commit", argv=["git", "commit", "-m", "x"])

    assert rule.evaluate(action) is None
    assert rule.evaluate(action) is None
    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY


def test_git_mutation_rate_limit_ignores_non_mutating_git_commands():
    rule = GitMutationRateLimitRule(max_mutations=1)
    action = PolicyAction(name="git_status", argv=["git", "status"])

    for _ in range(5):
        assert rule.evaluate(action) is None


def test_policy_denied_exception_carries_decision():
    engine = PolicyEngine(rules=[ForbiddenCommandRule()])
    action = PolicyAction(name="git_push", argv=["git", "push", "--force"])
    decision = engine.check(action)

    exc = PolicyDenied(decision)

    assert exc.decision is decision
    assert "forbidden_command" in str(exc)


def test_first_deny_wins_short_circuits_evaluation():
    """AuthorityRule (priority 1) should deny before AutomationSourceGuardRule
    (priority 5) even gets a chance, for a dangerous external git action."""
    engine = PolicyEngine(rules=default_rule_set())
    action = PolicyAction(
        name="git_merge",
        argv=["git", "merge", "active_task"],
        source=ActionSource.EXTERNAL,
        risk_level=RiskLevel.DANGEROUS,
    )

    decision = engine.check(action)

    assert decision.allowed is False
    assert decision.rule_name == "authority_hierarchy"


def test_import_boundary_rule_denies_forbidden_import():
    rule = ImportBoundaryRule(
        [LayerRule(layer="domain", path_patterns=["domain/*.py"], forbidden_imports=["infra"])]
    )
    action = PolicyAction(
        name="write_file", path="domain/order.py", content="import infra.db\n"
    )

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY
    assert "infra" in decision.reason


def test_import_boundary_rule_allows_permitted_import():
    rule = ImportBoundaryRule(
        [LayerRule(layer="domain", path_patterns=["domain/*.py"], forbidden_imports=["infra"])]
    )
    action = PolicyAction(
        name="write_file", path="domain/order.py", content="import collections\n"
    )

    assert rule.evaluate(action) is None


def test_import_boundary_rule_abstains_outside_declared_layers():
    rule = ImportBoundaryRule(
        [LayerRule(layer="domain", path_patterns=["domain/*.py"], forbidden_imports=["infra"])]
    )
    action = PolicyAction(
        name="write_file", path="scripts/tool.py", content="import infra.db\n"
    )

    assert rule.evaluate(action) is None


def test_import_boundary_rule_abstains_without_content():
    rule = ImportBoundaryRule(
        [LayerRule(layer="domain", path_patterns=["domain/*.py"], forbidden_imports=["infra"])]
    )
    assert rule.evaluate(PolicyAction(name="write_file", path="domain/order.py")) is None


def test_import_boundary_rule_catches_from_import_variant():
    rule = ImportBoundaryRule(
        [LayerRule(layer="domain", path_patterns=["domain/*.py"], forbidden_imports=["infra"])]
    )
    action = PolicyAction(
        name="write_file", path="domain/order.py", content="from infra.db import Session\n"
    )

    decision = rule.evaluate(action)

    assert decision is not None
    assert decision.decision == Decision.DENY
