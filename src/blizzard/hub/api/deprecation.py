"""The shared deprecation-header helper for a retired-but-kept-working alias route
(issue #104).

Every alias route (a) declares ``deprecated=True`` on its ``@router.<verb>(...)``
decorator so the OpenAPI operation carries the marker, (b) takes a ``response:
Response`` param, (c) calls the successor handler function directly so status code
and body are byte-identical, then (d) calls :func:`mark_deprecated` before
returning — delegation-by-call, not an HTTP redirect, so the alias stays exactly as
correct as its successor.
"""

from __future__ import annotations

from fastapi import Response

HEADER_DEPRECATION = "Deprecation"
HEADER_LINK = "Link"
HEADER_SUNSET = "Sunset"


def mark_deprecated(response: Response, *, successor: str, sunset: str | None = None) -> None:
    """Mark ``response`` as coming from a deprecated route whose replacement is
    ``successor`` — sets ``Deprecation: true`` and a ``Link: <successor>;
    rel="successor-version"`` header, plus ``Sunset`` when given."""
    response.headers[HEADER_DEPRECATION] = "true"
    response.headers[HEADER_LINK] = f'<{successor}>; rel="successor-version"'
    if sunset is not None:
        response.headers[HEADER_SUNSET] = sunset
