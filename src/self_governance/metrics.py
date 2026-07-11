"""Prometheus Metrics module.

Defines Prometheus instrumentation components (Counter, Histogram)
for observing succession loops, latency, swarm cost, and webhook events.
"""

from prometheus_client import Counter, Histogram

# Prometheus metrics for ASG platform observation
ASG_CONSENSUS_ITERATIONS = Counter(
    "asg_consensus_iterations_total",
    "Total number of consensus loops and rounds run during Succession sessions",
)

ASG_PIPELINE_LATENCY = Histogram(
    "asg_pipeline_latency_seconds",
    "Seconds taken to execute dynamic swarm pipeline phases",
    labelnames=["phase"],
)

ASG_SWARM_COST_USD = Counter(
    "asg_swarm_cost_usd",
    "Accumulated dollar cost of LLM token usages during development and reviews",
)

ASG_WEBHOOK_EVENTS = Counter(
    "asg_webhook_events_total",
    "Total number of webhook events received by the FastAPI webhook handler",
    labelnames=["event_type"],
)

