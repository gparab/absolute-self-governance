"""Forbidden subprocess argv patterns for the Ship Phase's git/pytest calls."""

from self_governance.policy import ActionSource, Decision, PolicyAction, PolicyDecision

# Argv substrings that must never appear together in a policed action's argv,
# regardless of which git/shell command carries them. Checked as a set
# (order-independent), so "git push --force origin master" and
# "git push origin master --force" are caught the same way.
_FORBIDDEN_ARGV_COMBOS = [
    {"push", "--force"},
    {"push", "-f"},
    {"reset", "--hard"},
    {"clean", "-fdx"},
    {"rm", "-rf"},
]

_PROTECTED_BRANCHES = {"master", "main"}


class ForbiddenCommandRule:
    """Denies subprocess argv matching a known-destructive pattern."""

    name = "forbidden_command"
    priority = 10

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv:
            return None
        argv_set = set(action.argv)
        for combo in _FORBIDDEN_ARGV_COMBOS:
            if combo.issubset(argv_set):
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=f"argv matches forbidden pattern {sorted(combo)}: {action.argv}",
                )
        return None


class ProtectedBranchDeletionRule:
    """Denies branch deletion targeting master/main, even without -D."""

    name = "protected_branch_deletion"
    priority = 11

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or "branch" not in action.argv:
            return None
        argv_set = set(action.argv)
        deleting = bool(argv_set & {"-d", "-D", "--delete"})
        if deleting and (argv_set & _PROTECTED_BRANCHES):
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"attempted deletion of a protected branch: {action.argv}",
            )
        return None


class AutomationSourceGuardRule:
    """Only the nudger's own trusted code path may run git-mutating actions.

    A God's Eye interrupt or an external/webhook-triggered action has no
    business invoking git merge/commit/push directly -- those only ever
    happen from ASG's own Ship Phase. This is a structural guarantee, not
    just "we don't currently wire it that way."
    """

    name = "automation_source_guard"
    priority = 5

    _MUTATING_GIT_SUBCOMMANDS = {"merge", "commit", "push", "reset", "clean"}

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or action.argv[0] != "git":
            return None
        if action.source == ActionSource.NUDGER:
            return None
        if set(action.argv) & self._MUTATING_GIT_SUBCOMMANDS:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"mutating git action from untrusted source {action.source.value}: {action.argv}",
            )
        return None
