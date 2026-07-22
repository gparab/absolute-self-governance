"""Caps self-modifying git operations within a rolling time window, so a
runaway loop can't hammer git indefinitely even if every individual call
would otherwise be allowed."""

import time
from collections import deque
from typing import Callable, Deque

from self_governance.policy import Decision, PolicyAction, PolicyDecision

_MUTATING_GIT_SUBCOMMANDS = {"merge", "commit", "push", "worktree"}


class GitMutationRateLimitRule:
    """Denies git-mutating actions once more than max_mutations have
    occurred within the trailing window_seconds.

    Time-windowed, not a per-process lifetime total (peer-review batch,
    July 2026): the original version tracked a running count with no
    reset, which for a long-running ContinuousNudger daemon meant a hard
    ceiling on total git mutations ever -- at ~4-5 mutations per
    successful Ship Phase cycle, a default of 500 permanently starves the
    daemon after roughly 100 cycles, requiring a manual restart to recover.
    A rolling window enforces the same "no runaway loop" intent without
    that permanent-lockup failure mode: a burst still gets capped, but
    mutation capacity recovers as old entries age out of the window.
    """

    name = "git_mutation_rate_limit"
    priority = 30

    def __init__(
        self,
        max_mutations: int = 500,
        window_seconds: float = 3600.0,
        time_fn: Callable[[], float] = time.monotonic,
    ):
        self.max_mutations = max_mutations
        self.window_seconds = window_seconds
        self._time_fn = time_fn
        self._timestamps: Deque[float] = deque()

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if not action.argv or action.argv[0] != "git":
            return None
        if not set(action.argv) & _MUTATING_GIT_SUBCOMMANDS:
            return None

        now = self._time_fn()
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

        if len(self._timestamps) >= self.max_mutations:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=(
                    f"git mutation count ({len(self._timestamps)}) exceeds ceiling "
                    f"({self.max_mutations}) within the trailing {self.window_seconds:.0f}s window"
                ),
            )

        self._timestamps.append(now)
        return None
