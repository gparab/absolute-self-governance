"""Risk-tiered sandbox backend routing (AgenticX's pattern, July 2026
topic-page batch): informational-only mapping from a PolicyAction's
RiskLevel to a suggested sandbox backend tier. Not wired into an
enforcement decision -- ASG's actual sandbox (gemini_adapter.py's
execute_tests) is docker-only today; this exists so a future caller
choosing between backends (e.g. an in-process check vs. a full container)
has a single, auditable place to ask "how much isolation does this risk
level warrant" instead of hardcoding the answer at each call site."""

from self_governance.policy import RiskLevel

# Ordered least to most isolated. FORBIDDEN has no tier: PolicyEngine denies
# it outright, so it should never reach a sandbox at all.
_TIER_BY_RISK = {
    RiskLevel.SAFE: "in_process",
    RiskLevel.CAUTION: "subprocess",
    RiskLevel.DANGEROUS: "container",
}


def suggest_sandbox_tier(risk_level: RiskLevel) -> str:
    """Returns the suggested sandbox backend tier for a given risk level.

    Raises:
        ValueError: For RiskLevel.FORBIDDEN -- a forbidden action should
            never reach the point of choosing a sandbox tier for it.
    """
    if risk_level == RiskLevel.FORBIDDEN:
        raise ValueError("FORBIDDEN actions must be denied, not sandboxed")
    return _TIER_BY_RISK[risk_level]
