"""Forbidden subprocess argv patterns for the Ship Phase's git/pytest calls."""

import base64
import binascii
import re

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
    """Denies branch deletion targeting master/main, whether local
    (`git branch -d/-D/--delete <branch>`) or remote (`git push origin
    --delete <branch>`, or the `:<branch>` refspec-delete shorthand).

    The original version only checked local `branch` deletion -- requiring
    the literal token "branch" in argv -- so `git push origin --delete
    main` bypassed it entirely (peer-review batch, July 2026): `push` isn't
    `branch`, so the rule abstained immediately.
    """

    name = "protected_branch_deletion"
    priority = 11

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv:
            return None
        argv_set = set(action.argv)

        if "branch" in argv_set:
            deleting = bool(argv_set & {"-d", "-D", "--delete"})
            if deleting and (argv_set & _PROTECTED_BRANCHES):
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=f"attempted deletion of a protected branch: {action.argv}",
                )
            return None

        if "push" in argv_set:
            if argv_set & {"--delete", "-d"} and argv_set & _PROTECTED_BRANCHES:
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=f"attempted remote deletion of a protected branch: {action.argv}",
                )
            for token in action.argv:
                # Refspec-delete shorthand: an empty source before the colon
                # (":main" or "origin :main") deletes the remote ref. Strip
                # an optional leading "+" (peer-review batch, July 2026):
                # git's force-push prefix means "+:main" deletes main just
                # like ":main" does, but token.startswith(":") alone missed
                # it entirely.
                clean_token = token.lstrip("+")
                if clean_token.startswith(":") and clean_token[1:] in _PROTECTED_BRANCHES:
                    return PolicyDecision(
                        decision=Decision.DENY,
                        rule_name=self.name,
                        reason=f"attempted remote deletion of a protected branch via refspec: {action.argv}",
                    )
            return None

        return None


_B64_RE = re.compile(r"[A-Za-z0-9+/]{16,}={0,2}")


def _normalize_argv(argv: "list[str]") -> "set[str]":
    """Widens the forbidden-combo match beyond literal argv tokens (AgentTrust,
    research.google survey, July 2026 topic-page batch): decodes any
    base64-looking token and folds common `echo -e`/printf escape shorthands,
    so `git push $(echo cC1m|base64 -d)` still lands on the same forbidden
    tokens as a literal `-f`. Best-effort -- unrecognized tokens pass through
    unchanged, this is a widening of the match set, never a narrowing."""
    normalized: "set[str]" = set()
    for token in argv:
        normalized.add(token)
        candidate = token
        padded = candidate + "=" * (-len(candidate) % 4)
        if _B64_RE.fullmatch(candidate):
            try:
                decoded = base64.b64decode(padded, validate=True).decode("utf-8", errors="strict")
                normalized.update(decoded.split())
            except (binascii.Error, ValueError, UnicodeDecodeError):
                pass
    return normalized


class ObfuscatedCommandRule:
    """Re-runs the forbidden-combo check against a normalized (obfuscation-
    decoded) view of argv, catching base64-wrapped destructive flags that
    ForbiddenCommandRule's literal match misses."""

    name = "obfuscated_command"
    priority = 9

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv:
            return None
        normalized = _normalize_argv(action.argv)
        if normalized == set(action.argv):
            return None  # nothing decoded -- ForbiddenCommandRule already covers this
        for combo in _FORBIDDEN_ARGV_COMBOS:
            if combo.issubset(normalized):
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=f"argv decodes to forbidden pattern {sorted(combo)}: {action.argv}",
                )
        return None


# Command sequences that are individually benign but form a known attack
# chain in aggregate (AgentTrust's "RiskChain" pattern): download-and-execute.
_RISKY_SEQUENCES = [
    ("curl", "chmod", "execute"),
    ("wget", "chmod", "execute"),
]


def _classify(argv: "list[str]") -> "str | None":
    argv_set = set(argv)
    if {"curl"} & argv_set:
        return "curl"
    if {"wget"} & argv_set:
        return "wget"
    if {"chmod"} & argv_set and ({"+x", "755", "777"} & argv_set):
        return "chmod"
    if argv and argv[0] not in ("git", "curl", "wget", "chmod"):
        return "execute"
    return None


def _contains_subsequence_in_order(window: "list[str]", seq: "tuple[str, ...]") -> bool:
    """True if seq's elements all appear in window, in order, not
    necessarily contiguously."""
    it = iter(window)
    return all(step in it for step in seq)


class CommandSequenceRule:
    """Stateful (like GitMutationRateLimitRule): tracks a short rolling
    window of recent command classes per instance and flags a run that
    completes a known risky sequence (e.g. download -> make executable ->
    run), even though each individual step passes every other rule.

    Matches as an in-order subsequence, not a strictly contiguous one
    (peer-review batch, July 2026): the original tuple(self._recent[-n:])
    == seq check required the risky steps to be immediately adjacent, so
    interleaving one harmless command between them (curl, ls, chmod,
    ./payload -- "ls" classifies as the catch-all "execute" step just like
    the real payload does, shifting the tail and breaking the match)
    bypassed detection entirely.
    """

    name = "command_sequence"
    priority = 12
    _WINDOW = 5

    def __init__(self) -> None:
        self._recent: "list[str]" = []

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        step = _classify(action.argv)
        if step is None:
            return None
        self._recent.append(step)
        self._recent = self._recent[-self._WINDOW :]
        for seq in _RISKY_SEQUENCES:
            if _contains_subsequence_in_order(self._recent, seq):
                return PolicyDecision(
                    decision=Decision.DENY,
                    rule_name=self.name,
                    reason=f"command sequence matches known attack chain {seq}",
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
