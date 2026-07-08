import logging
import os
import stripe
from sqlalchemy.orm import Session
from self_governance.db import TokenUsage, Tenant

logger = logging.getLogger("self_governance.billing")

stripe.api_key = os.getenv("STRIPE_API_KEY")


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

    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    stripe_customer_id = tenant.stripe_customer_id if tenant else None

    # Trigger billing system integration
    report_usage_to_stripe(tenant_id, cost_usd, stripe_customer_id)
    return usage


def report_usage_to_stripe(
    tenant_id: str, cost_usd: float, stripe_customer_id: str = None
) -> None:
    """Report usage-based LLM costs to Stripe meters."""
    logger.info(
        "Reporting metered usage to Stripe: tenant=%s, cost=$%.6f, customer=%s",
        tenant_id,
        cost_usd,
        stripe_customer_id,
    )
    if not stripe.api_key:
        logger.warning("STRIPE_API_KEY is not set. Skipping Stripe reporting.")
        return

    if not stripe_customer_id:
        logger.warning(
            "No stripe_customer_id found for tenant %s. Skipping.", tenant_id
        )
        return

    try:
        stripe.billing.MeterEvent.create(
            event_name="llm_token_usage",
            payload={
                "value": str(cost_usd),
                "stripe_customer_id": stripe_customer_id,
            },
        )
    except Exception as e:
        logger.error("Failed to report usage to Stripe: %s", str(e))
