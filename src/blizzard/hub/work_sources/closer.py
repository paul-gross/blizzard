"""The write-half work-source seam's close capability — delivery-time item closure."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef


class WorkCloseError(Exception):
    """The forge write for closure failed — an unreachable forge, a rate limit, or
    an insufficient-scope token."""

    def __init__(self, message: str = "", *, retry_after: datetime | None = None) -> None:
        super().__init__(message)
        #: Set only when the forge's rate budget turned the call away or the forge refused it for rate: when to retry.
        self.retry_after = retry_after


class WorkItemGoneError(WorkCloseError):
    """The work item no longer exists at the source (404/410)."""


class IWorkCloser(Protocol):
    """One configured, credentialed work-source binding's close half."""

    def close(self, pointer: WorkRef, *, trace: DeliveryTrace | None) -> None:
        """Close ``pointer`` idempotently; ``trace`` (required) names what landed it, ``None`` if
        nothing did. Raises :class:`WorkItemGoneError` if it no longer exists, else :class:`WorkCloseError`."""
        ...
