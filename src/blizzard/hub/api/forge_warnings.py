"""What a write answers when it named its credential by an old secret field alone: a ``Deprecation`` header, a
``warnings`` entry per field, and one ``config-old-field-used`` event per field, so an operator can see when nothing
still sends one. Every door that takes the old fields answers through :func:`warned`."""

from __future__ import annotations

from collections.abc import Sequence

from fastapi import Response

from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.config.authoring import OldFieldUse
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.wire.forge import OldFieldWarning

#: The ``Deprecation`` response header a write that used an old field answers with.
DEPRECATION_HEADER = ("Deprecation", "true")

_EVENT_OLD_FIELD_USED: EventLogKind = "config-old-field-used"


def warned(
    response: Response,
    services: TenantServices,
    used: Sequence[OldFieldUse],
    *,
    record: str,
    forge: ConfiguredForge,
) -> list[OldFieldWarning]:
    """The wire warnings for ``used``, the old fields the write of ``record`` on ``forge`` resolved to a credential,
    with the header set and an event recorded per field. Called once the write has committed."""
    if not used:
        return []
    name, value = DEPRECATION_HEADER
    response.headers[name] = value
    now = services.clock.now()
    warnings: list[OldFieldWarning] = []
    for use in used:
        credential = use.credential
        message = f"`{use.field}` is deprecated; it resolved to credential {credential.slug} — send `credential`"
        services.event_log.record(
            kind=_EVENT_OLD_FIELD_USED,
            runner_id=None,
            chunk_id=None,
            project_id=None,
            lease_id=None,
            node_name=None,
            message=f"{record}: {message}",
            detail={
                "record": record,
                "field": use.field,
                "forge_id": forge.forge_id,
                "forge_slug": forge.slug,
                "credential_id": credential.credential_id,
                "credential_slug": credential.slug,
            },
            at=now,
        )
        warnings.append(
            OldFieldWarning(field=use.field, forge_id=forge.forge_id, forge_slug=forge.slug, message=message)
        )
    return warnings
