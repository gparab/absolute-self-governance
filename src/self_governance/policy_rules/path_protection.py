"""Protects ASG's own governance state files from writes originating outside
the nudger's own trusted Ship Phase code path."""

import os
from typing import Optional

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

# Directory names that must never be written into by a non-NUDGER-sourced
# action, regardless of the target basename (peer-review batch, July 2026):
# _PROTECTED_BASENAMES alone only blocked specific known filenames, but
# ResilientHookExecutor (nudger.py) auto-executes *any* file it finds in
# hooks/ matching a lifecycle hook name, unsandboxed. A write of a new file
# there -- not just an overwrite of a known one -- is the actual attack
# surface.
_PROTECTED_DIRS = {"hooks"}


class ProtectedFileWriteRule:
    """Denies writes to governance state files/directories from non-nudger sources.

    Known gap: this rule only gates actions that go through PolicyEngine as
    an explicit file-write PolicyAction. It does NOT gate content that
    arrives via `git merge` -- the Ship Phase's merge of a worktree branch
    back into the main working directory is itself a NUDGER-sourced,
    trusted action, so a file an LLM-authored commit added under hooks/ in
    the worktree can still land there via merge, unexamined by this rule.
    Closing that gap needs content-provenance tracking through the merge
    itself, not a path-protection rule -- out of scope here.
    """

    name = "protected_file_write"
    priority = 20

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.path:
            return None
        if action.source == ActionSource.NUDGER:
            return None
        basename = os.path.basename(action.path)
        parts = os.path.normpath(action.path).split(os.sep)
        if basename in _PROTECTED_BASENAMES:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"write to protected file '{basename}' from untrusted source {action.source.value}",
            )
        if _PROTECTED_DIRS & set(parts):
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"write into protected directory (path '{action.path}') from untrusted source {action.source.value}",
            )
        return None


class WorktreePathTraversalRule:
    """Denies a worktree path argument that escapes the working directory.

    Checking only for ".." segments (peer-review batch, July 2026) missed
    an absolute path with no ".." at all -- e.g. "/etc/cron.d/malicious" --
    which normalizes clean and would have been allowed. Now requires the
    normalized, absolute target to actually be under the working directory,
    via os.path.commonpath rather than a segment scan.
    """

    name = "worktree_path_traversal"
    priority = 6

    def __init__(self, working_directory: Optional[str] = None) -> None:
        self.working_directory = working_directory

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or "worktree" not in action.argv:
            return None
        if not action.path:
            return None

        base = os.path.abspath(self.working_directory or os.getcwd())
        target = os.path.abspath(os.path.join(base, action.path))
        try:
            escapes = os.path.commonpath([base, target]) != base
        except ValueError:
            # commonpath raises on e.g. mixed drives on Windows -- treat as
            # an escape rather than silently allowing an unrecognized path.
            escapes = True

        if escapes:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=f"worktree path '{action.path}' escapes the working directory",
            )
        return None
