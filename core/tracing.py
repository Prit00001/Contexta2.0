"""OpenTelemetry tracing (P2-15), off by default.

Spans: one per HTTP request (``app.observability``), one per research-workflow stage
(``services.research_graph``) and one per LLM provider call (``core.generator``), nested in that
order, so a trace shows where a slow answer spent its time.

* The code uses only ``opentelemetry-api``. Without an SDK configured every span is a no-op, which is
  the default: nothing is recorded or sent anywhere.
* ``TRACING_ENABLED=true`` installs the SDK with an OTLP/HTTP exporter (``requirements-otel.txt``,
  not in the default image). Where traces go is configured with the standard ``OTEL_*`` variables,
  e.g. ``OTEL_EXPORTER_OTLP_ENDPOINT`` and ``OTEL_TRACES_SAMPLER_ARG``.
* Same privacy rule as the logs: span attributes carry stage names, statuses, durations, providers,
  models and counts - never the question, answer, source text or any credential.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import trace

log = logging.getLogger("contexta.tracing")
tracer = trace.get_tracer("contexta")

_provider = None  # the SDK TracerProvider this module installed, if any


def configure_tracing(enabled: bool, service_name: str = "contexta") -> bool:
    """Install the OTLP exporter when ``enabled``. Returns whether spans are now exported. Missing
    SDK packages are logged and ignored: tracing must never stop the API from starting."""
    global _provider
    if not enabled or _provider is not None:
        return _provider is not None
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        log.warning(
            "TRACING_ENABLED is set but the OpenTelemetry SDK is not installed "
            "(pip install -r requirements-otel.txt); tracing stays off",
            extra={"event": "startup.tracing", "status": "unavailable"},
        )
        return False
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    _provider = provider
    log.info("Tracing on: OTLP/HTTP exporter", extra={"event": "startup.tracing", "status": "ok"})
    return True


def shutdown_tracing() -> None:
    """Flush queued spans on shutdown (no-op when tracing is off)."""
    if _provider is not None:
        _provider.shutdown()


@contextmanager
def span(name: str, **attributes) -> Iterator[trace.Span]:
    """A span with ``contexta.``-prefixed attributes; None values are dropped."""
    with tracer.start_as_current_span(name) as s:
        set_attributes(s, **attributes)
        yield s


def set_attributes(s: trace.Span, **attributes) -> None:
    if s.is_recording():
        s.set_attributes({f"contexta.{k}": v for k, v in attributes.items() if v is not None})
