import logging
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger("self_governance.tracing")

# Initialize OpenTelemetry Tracer
provider = TracerProvider()
if os.getenv("TESTING") != "True":
    try:
        processor = BatchSpanProcessor(ConsoleSpanExporter())
        provider.add_span_processor(processor)
    except Exception as e:
        logger.warning("Could not initialize OTel console exporter: %s", e)

trace.set_tracer_provider(provider)
tracer = trace.get_tracer("self_governance")
