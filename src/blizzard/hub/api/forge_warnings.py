"""What a write answers when it named its forge by an old address field: a ``Deprecation`` header and a ``warnings``
entry per field. Every door that takes the old fields answers through :func:`warned`; the write itself records the
``config-old-field-used`` events."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Response

from blizzard.hub.domain.config.forge_resolution import OldFieldUsed
from blizzard.wire.forge import OldFieldWarning

#: The ``Deprecation`` response header (RFC 9745) a write that used an old field answers with: the instant the old
#: address fields were deprecated, 2026-10-25T00:00:00Z, as a structured-field date.
DEPRECATION_HEADER = ("Deprecation", "@1792886400")


def warned(response: Response, used: Sequence[OldFieldUsed]) -> list[OldFieldWarning]:
    """The wire warnings for ``used``, the old fields the write leaned on, with the header set when there are any."""
    if not used:
        return []
    name, value = DEPRECATION_HEADER
    response.headers[name] = value
    return [
        OldFieldWarning(
            field=warning.field, forge_id=warning.forge_id, forge_slug=warning.forge_slug, message=warning.message
        )
        for warning in used
    ]
