"""Default policy rule set for ASG's Ship Phase actions."""

from typing import List

from self_governance.policy import PolicyRule
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


def default_rule_set() -> List[PolicyRule]:
    """Builds the standard rule set used by nudger.py's Ship Phase.

    Returns a fresh rate-limit rule instance per call, since it's stateful
    and scoped to one nudger process lifetime -- callers that want a
    shared ceiling across engine instances should build their own list.
    """
    return [
        AuthorityRule(),
        AutomationSourceGuardRule(),
        WorktreePathTraversalRule(),
        ForbiddenCommandRule(),
        ProtectedBranchDeletionRule(),
        ProtectedFileWriteRule(),
        GitMutationRateLimitRule(),
    ]
