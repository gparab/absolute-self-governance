"""Caps self-modifying git operations per nudger process lifetime, so a
runaway loop can't hammer git indefinitely even if every individual call
would otherwise be allowed."""

from self_governance.policy import Decision, PolicyAction, PolicyDecision

_MUTATING_GIT_SUBCOMMANDS = {"merge", "commit", "push", "worktree"}


class GitMutationRateLimitRule:
    """Denies git-mutating actions once a per-instance ceiling is hit.

    Stateful by design (unlike the other stateless rules) -- tracks a
    running count on the rule instance itself, so one PolicyEngine
    (one nudger process) enforces one ceiling across its whole lifetime.
    """

    name = "git_mutation_rate_limit"
    priority = 30

    def __init__(self, max_mutations: int = 500):
        self.max_mutations = max_mutations
        self._count = 0

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or action.argv[0] != "git":
            return None
        if not set(action.argv) & _MUTATING_GIT_SUBCOMMANDS:
            return None
        self._count += 1
        if self._count > self.max_mutations:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"git mutation count ({self._count}) exceeds per-process ceiling ({self.max_mutations})",
            )
        return None
