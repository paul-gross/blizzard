"""The write-half work-source seam's close capability — delivery-time item closure."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef


class WorkCloseError(Exception):
    """The forge write for closure failed — an unreachable forge, a rate limit, or
    an insufficient-scope token."""


class WorkItemGoneError(WorkCloseError):
    """The work item no longer exists at the source (404/410)."""


class IWorkCloser(Protocol):
    """One configured, credentialed work-source binding's close half."""

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Close ``pointer`` idempotently; ``trace`` (required) names what landed it, ``None`` if
        nothing did. Raises :class:`WorkItemGoneError` if it no longer exists, else :class:`WorkCloseError`."""
        ...
