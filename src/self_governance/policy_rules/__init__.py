"""Default policy rule set for ASG's Ship Phase actions."""

from typing import List, Optional

from self_governance.policy import PolicyRule
from self_governance.policy_rules.authority import AuthorityRule
from self_governance.policy_rules.budget import BudgetConservationRule
from self_governance.policy_rules.command_safety import (
    AutomationSourceGuardRule,
    CommandSequenceRule,
    ForbiddenCommandRule,
    ObfuscatedCommandRule,
    ProtectedBranchDeletionRule,
)
from self_governance.policy_rules.path_protection import (
    ProtectedFileWriteRule,
    WorktreePathTraversalRule,
)
from self_governance.policy_rules.rate_limits import GitMutationRateLimitRule


def default_rule_set(working_directory: Optional[str] = None) -> List[PolicyRule]:
    """Builds the standard rule set used by nudger.py's Ship Phase.

    Returns fresh stateful-rule instances per call (rate limit, command
    sequence), since both are scoped to one nudger process lifetime --
    callers that want a shared ceiling across engine instances should build
    their own list.

    Args:
        working_directory: Passed to WorktreePathTraversalRule as the base
            a worktree path must stay under. None falls back to the
            process's own cwd at evaluation time.
    """
    return [
        AuthorityRule(),
        AutomationSourceGuardRule(),
        BudgetConservationRule(),
        WorktreePathTraversalRule(working_directory=working_directory),
        ObfuscatedCommandRule(),
        ForbiddenCommandRule(),
        ProtectedBranchDeletionRule(),
        CommandSequenceRule(),
        ProtectedFileWriteRule(),
        GitMutationRateLimitRule(),
    ]
