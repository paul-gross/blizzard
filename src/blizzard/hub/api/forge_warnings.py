"""What a write answers when it named its forge by an old address field: a ``Deprecation`` header, a ``warnings``
entry per field, and one ``config-old-field-used`` event per field, so an operator can see when nothing still sends
one. Every door that takes the old fields answers through :func:`warned`."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Response

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.forge_resolution import OldFieldUsed
from blizzard.wire.forge import OldFieldWarning

#: The ``Deprecation`` response header a write that used an old field answers with.
DEPRECATION_HEADER = ("Deprecation", "true")

_EVENT_OLD_FIELD_USED: EventLogKind = "config-old-field-used"


def warned(
    response: Response,
    services: TenantServices,
    used: Sequence[OldFieldUsed],
    *,
    record: str,
    recorded: bool = True,
) -> list[OldFieldWarning]:
    """The wire warnings for ``used``, the old fields the write naming ``record`` leaned on, with the header set and —
    unless the write was not ``recorded``, as a dry run is not — an event recorded per field."""
    if not used:
        return []
    name, value = DEPRECATION_HEADER
    response.headers[name] = value
    if recorded:
        now = services.clock.now()
        for warning in used:
            services.event_log.record(
                kind=_EVENT_OLD_FIELD_USED,
                runner_id=None,
                chunk_id=None,
                project_id=None,
                lease_id=None,
                node_name=None,
                message=f"{record}: {warning.message}",
                detail={
                    "record": record,
                    "field": warning.field,
                    "forge_id": warning.forge_id,
                    "forge_slug": warning.forge_slug,
                },
                at=now,
            )
    return [
        OldFieldWarning(
            field=warning.field, forge_id=warning.forge_id, forge_slug=warning.forge_slug, message=warning.message
        )
        for warning in used
    ]
