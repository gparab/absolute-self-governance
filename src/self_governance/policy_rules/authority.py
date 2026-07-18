"""Authority hierarchy: only DANGEROUS/FORBIDDEN actions from a trusted
source may proceed. External and God's-Eye-sourced actions are capped at
CAUTION regardless of what they claim to need."""

from self_governance.policy import ActionSource, Decision, PolicyAction, PolicyDecision, RiskLevel


class AuthorityRule:
    """Denies DANGEROUS/FORBIDDEN-risk actions whose source isn't the nudger's
    own trusted code path."""

    name = "authority_hierarchy"
    priority = 1

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if action.source == ActionSource.NUDGER:
            return None
        if action.risk_level in (RiskLevel.DANGEROUS, RiskLevel.FORBIDDEN):
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=(
                    f"{action.risk_level.value}-risk action '{action.name}' "
                    f"from untrusted source {action.source.value}"
                ),
            )
        return None
