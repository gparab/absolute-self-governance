"""Budget conservation rule (Agent Contracts, research.google survey, July
2026 topic-page batch): denies an action once its AgentBudget is exhausted,
enforcing the delegation conservation law at the point actions are actually
gated rather than trusting callers to check budget.remaining themselves."""

from self_governance.policy import Decision, PolicyAction, PolicyDecision


class BudgetConservationRule:
    """Abstains (returns None) for actions with no budget attached -- this
    rule only applies once a caller opts an action into budget tracking by
    setting PolicyAction.budget."""

    name = "budget_conservation"
    priority = 6

    def evaluate(self, action: PolicyAction) -> "PolicyDecision | None":
        if action.budget is None:
            return None
        if action.budget.remaining <= 0:
            return PolicyDecision(
                decision=Decision.DENY,
                rule_name=self.name,
                reason=(
                    f"action '{action.name}' denied: budget exhausted "
                    f"({action.budget.spent}/{action.budget.max_actions} actions spent)"
                ),
            )
        action.budget.spent += 1
        return None
