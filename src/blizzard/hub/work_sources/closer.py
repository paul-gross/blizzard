"""The write-half work-source seam's close capability — delivery-time item closure.

A sibling Protocol to ``IWorkSource``'s read-only pass-through, so "this source may not
close" is a *presence* question the registry answers rather than a ``hasattr`` probe. Every
configured source's registry entry has a closer while the instance-level
``close_forge_writes_enabled`` is true; false leaves every one of them absent instead."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef


class WorkCloseError(Exception):
    """The forge write for closure failed — an unreachable forge, a rate limit, or
    an insufficient-scope token. Degrades to a recorded ``failed`` outcome the
    close-intent drainer retries on its next sweep; never raised past the sweep itself."""


class WorkItemGoneError(WorkCloseError):
    """The work item no longer exists at the source (404/410) — recorded as the
    terminal ``gone`` outcome, unlike the retried ``failed`` outcome a bare
    :class:`WorkCloseError` gets."""


class IWorkCloser(Protocol):
    """One configured, credentialed work-source binding's close half."""

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Close ``pointer`` idempotently; ``trace`` (required) names what landed it, ``None`` if
        nothing did. Raises :class:`WorkItemGoneError` if it no longer exists, else :class:`WorkCloseError`."""
        ...
