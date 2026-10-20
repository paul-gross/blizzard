"""The stage between the batch processor and the exporter: every finished span leaves without a
query string or fragment on any URL-bearing attribute."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from opentelemetry.util.types import Attributes

_DROPPED = frozenset({"url.query"})
_URLS = frozenset({"url.full", "http.url", "http.target"})


def _bare(value: object) -> object:
    if not isinstance(value, str):
        return value
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def redacted(attributes: Attributes) -> dict[str, object]:
    return {
        key: _bare(value) if key in _URLS else value for key, value in (attributes or {}).items() if key not in _DROPPED
    }


class RedactingExporter(SpanExporter):
    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return self._inner.export([self._rebuilt(span) for span in spans])

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._inner.force_flush(timeout_millis)

    @staticmethod
    def _rebuilt(span: ReadableSpan) -> ReadableSpan:
        return ReadableSpan(
            name=span.name,
            context=span.context,
            parent=span.parent,
            resource=span.resource,
            attributes=redacted(span.attributes),  # pyright: ignore[reportArgumentType]
            events=span.events,
            links=span.links,
            kind=span.kind,
            status=span.status,
            start_time=span.start_time,
            end_time=span.end_time,
            instrumentation_scope=span.instrumentation_scope,
        )
