"""What a write answers when it named its credential by an old secret field alone: a ``Deprecation`` header, a
``warnings`` entry per field, and one ``config-old-field-used`` event per field, so an operator can see when nothing
still sends one. Every door that takes the old fields answers through :func:`warned`."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import Depends, Response

from blizzard.foundation.clock import IClock
from blizzard.foundation.event_log import EventLogKind
from blizzard.hub.api.auth_session import AdmittedTenant, tenant_request
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.config.authoring import OldFieldUse
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.wire.forge import OldFieldWarning

#: The ``Deprecation`` response header a write that used an old field answers with.
DEPRECATION_HEADER = ("Deprecation", "true")

_EVENT_OLD_FIELD_USED: EventLogKind = "config-old-field-used"


def warned(
    response: Response,
    event_log: EventLogService,
    clock: IClock,
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
    now = clock.now()
    warnings: list[OldFieldWarning] = []
    for use in used:
        credential = use.credential
        message = f"`{use.field}` is deprecated; it resolved to credential {credential.slug} — send `credential`"
        event_log.record(
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


@dataclass(frozen=True)
class OldFieldWarnings:
    """Where a committed write's old-field warnings are recorded, and the clock that stamps them."""

    event_log: EventLogService
    clock: IClock

    def warned(
        self, response: Response, used: Sequence[OldFieldUse], *, record: str, forge: ConfiguredForge
    ) -> list[OldFieldWarning]:
        """:func:`warned`, recorded on this log."""
        return warned(response, self.event_log, self.clock, used, record=record, forge=forge)


@inject_sync
def old_field_warnings(
    _tenant: Annotated[AdmittedTenant, Depends(tenant_request)],
    event_log: FromDishka[EventLogService],
    clock: FromDishka[IClock],
) -> OldFieldWarnings:
    """The request's :class:`OldFieldWarnings`, each resolved through its scope."""
    return OldFieldWarnings(event_log=event_log, clock=clock)


Warnings = Annotated[OldFieldWarnings, Depends(old_field_warnings)]
