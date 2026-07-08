import logging
from sqlalchemy.orm import Session
from self_governance.db import TokenUsage, Tenant

logger = logging.getLogger("self_governance.billing")

def record_usage(
    tenant_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    db: Session
) -> TokenUsage:
    """Saves API token usage and calculated dollar cost to the database."""
    usage = TokenUsage(
        tenant_id=tenant_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd
    )
    db.add(usage)
    db.commit()
    db.refresh(usage)
    
    # Trigger billing system integration
    report_usage_to_stripe(tenant_id, cost_usd)
    return usage

def report_usage_to_stripe(tenant_id: str, cost_usd: float) -> None:
    """Mock connector reporting usage-based LLM costs to Stripe meters."""
    logger.info(
        "Reporting metered usage to Stripe: tenant=%s, cost=$%.6f",
        tenant_id,
        cost_usd
    )
