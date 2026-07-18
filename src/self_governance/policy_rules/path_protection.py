"""Protects ASG's own governance state files from writes originating outside
the nudger's own trusted Ship Phase code path."""

import os

from self_governance.policy import ActionSource, Decision, PolicyAction, PolicyDecision

# Basenames that must never be written by a non-NUDGER-sourced action.
# handoff.md/CURRENT_STATE.md is the succession control file; writing it
# from an untrusted source is how a malicious actor would forge a
# succession approval outright.
_PROTECTED_BASENAMES = {
    "handoff.md",
    "CURRENT_STATE.md",
    "automaton.json",
    "self_governance.db",
    "constitution.md",
}


class ProtectedFileWriteRule:
    """Denies writes to governance state files from non-nudger sources."""

    name = "protected_file_write"
    priority = 20

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.path:
            return None
        basename = os.path.basename(action.path)
        if basename in _PROTECTED_BASENAMES and action.source != ActionSource.NUDGER:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"write to protected file '{basename}' from untrusted source {action.source.value}",
            )
        return None


class WorktreePathTraversalRule:
    """Denies a worktree path argument that escapes the working directory."""

    name = "worktree_path_traversal"
    priority = 6

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or "worktree" not in action.argv:
            return None
        if not action.path:
            return None
        normalized = os.path.normpath(action.path)
        if ".." in normalized.split(os.sep):
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"worktree path '{action.path}' contains a traversal segment",
            )
        return None
