import logging
from sqlalchemy.orm import Session
from self_governance.db import TokenUsage

logger = logging.getLogger("self_governance.billing")


def record_usage(
    tenant_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    db: Session,
) -> TokenUsage:
    """Saves API token usage and calculated dollar cost to the database."""
    usage = TokenUsage(
        tenant_id=tenant_id,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        cost_usd=cost_usd,
    )
    db.add(usage)
    db.commit()
    db.refresh(usage)
    logger.info(
        "Recorded usage: tenant=%s, tokens=%d, cost=$%.6f",
        tenant_id,
        prompt_tokens + completion_tokens,
        cost_usd,
    )
    return usage
