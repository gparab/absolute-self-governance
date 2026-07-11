"""Billing and usage logging module.

Provides helpers to estimate costs for LLM tokens and save usage statistics
to the database for billing or budget auditing.
"""

import logging
from sqlalchemy.orm import Session
from self_governance.db import TokenUsage

logger = logging.getLogger("self_governance.billing")


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> float:
    """Calculates estimated cost in USD for Gemini token usage.

    Args:
        prompt_tokens: Number of prompt tokens used.
        completion_tokens: Number of completion tokens generated.

    Returns:
        Estimated cost of the call in USD.
    """
    return (prompt_tokens * 0.000000075) + (completion_tokens * 0.00000030)


def record_usage(
    tenant_id: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    db: Session,
) -> TokenUsage:
    """Saves API token usage and calculated dollar cost to the database.

    Args:
        tenant_id: The identifier of the tenant.
        prompt_tokens: Number of prompt tokens used.
        completion_tokens: Number of completion tokens generated.
        cost_usd: Estimated cost of the execution in USD.
        db: Database session.

    Returns:
        The created TokenUsage database record.
    """
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

