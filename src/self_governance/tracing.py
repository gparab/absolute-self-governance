"""OpenTelemetry Tracing Setup module.

Configures OTLP/Console span exporters and registers the default tracer provider
for distributed request tracing.
"""

import logging
import os
import threading
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger("self_governance.tracing")

# Initialize OpenTelemetry Tracer.
# OTEL_EXPORTER_OTLP_ENDPOINT set -> ship spans there; otherwise console.
provider = TracerProvider()
trace.set_tracer_provider(provider)
tracer = trace.get_tracer("self_governance")

if os.getenv("TESTING") != "True":
    def _setup_exporter():
        try:
            if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT"):
                from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                    OTLPSpanExporter,
                )

                provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(timeout=2)))
            else:
                provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        except Exception as e:
            logger.warning("Could not initialize OTel exporter: %s", e)

    # Do not block module load: initialize exporter in background thread
    threading.Thread(target=_setup_exporter, daemon=True).start()

