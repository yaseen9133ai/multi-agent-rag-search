"""OpenTelemetry instrumentation for the agent.

We don't change agent logic. OpenInference's ADK instrumentor auto-wraps ADK's Runner, agents, model
calls, and tool calls — every run becomes structured spans. Enable with OBSERVABILITY_ENABLED=true;
otherwise this is a no-op.

Two backends, picked from what you've configured:

  • Local dev → Phoenix:  set OTEL_EXPORTER_OTLP_ENDPOINT (docker-compose points it at Phoenix).
    We export raw OTLP to it.
  • Production → Langfuse: set LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY (+ optional LANGFUSE_HOST).
    We use the Langfuse SDK. (The same spans can be sent to Langfuse over raw OTLP, but its UI can take
    minutes to render them; the SDK shows them in seconds — so that's what we use in the cloud.)

Privacy: set OPENINFERENCE_HIDE_INPUTS / OPENINFERENCE_HIDE_OUTPUTS=true to keep span structure
without prompt/response payloads. (No code needed — OpenInference reads those env vars.)
"""
import logging
import os

logger = logging.getLogger(__name__)


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def init_observability() -> bool:
    """Instrument ADK and send spans to Phoenix (OTLP) or Langfuse (SDK). Returns True if enabled.

    Failures are logged, never raised — observability must never take down the agent it observes.
    """
    if not _truthy(os.environ.get("OBSERVABILITY_ENABLED")):
        logger.info("Observability disabled (set OBSERVABILITY_ENABLED=true to enable).")
        return False

    try:
        from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    except ImportError as exc:  # pragma: no cover - dependency guard
        logger.error("Observability deps missing (%s).", exc)
        return False

    # Local dev: an explicit OTLP endpoint (Phoenix) takes precedence.
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT")
    if endpoint:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        service = os.environ.get("OTEL_SERVICE_NAME", "multi-agent-rag-search")
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        GoogleADKInstrumentor().instrument(tracer_provider=provider)
        # Clear the OTLP endpoint vars so ADK's own env-driven setup doesn't add a second, conflicting
        # exporter (it also POSTs metrics to a path Phoenix doesn't have). We've already captured it.
        for v in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
                  "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT", "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT"):
            os.environ.pop(v, None)
        logger.info("Observability enabled → Phoenix/OTLP at %s", endpoint)
        return True

    # Production: Langfuse via its SDK (reads LANGFUSE_PUBLIC_KEY / SECRET_KEY / HOST from the env).
    if os.environ.get("LANGFUSE_PUBLIC_KEY"):
        try:
            from langfuse import get_client

            if not get_client().auth_check():
                logger.error("Langfuse auth failed — check LANGFUSE_* keys and host region.")
                return False
            GoogleADKInstrumentor().instrument()
        except Exception as exc:  # pragma: no cover - never crash the agent over telemetry
            logger.error("Langfuse setup failed (%s).", exc)
            return False
        logger.info("Observability enabled → Langfuse")
        return True

    logger.warning("OBSERVABILITY_ENABLED is set but neither OTEL_EXPORTER_OTLP_ENDPOINT nor "
                   "LANGFUSE_PUBLIC_KEY is configured — nothing to export to. Skipping.")
    return False
