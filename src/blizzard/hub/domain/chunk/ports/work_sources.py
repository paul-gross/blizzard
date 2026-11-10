"""The work-source ports the hub's closure and forge-status passes consume, and the errors their writes raise.

The work-source package implements them (``bzh:dependency-inversion``). :class:`IWorkClosers` and
:class:`IWorkAnnotators` are each pass's narrow slice of the work-source registry (``bzh:narrow-seams``); the registry
satisfies both structurally."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.chunk.delivery_read import DeliveryTrace
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.chunk.status_marker import WorkStatusMarker

__all__ = [
    "IWorkAnnotator",
    "IWorkAnnotators",
    "IWorkCloser",
    "IWorkClosers",
    "WorkAnnotateError",
    "WorkCloseError",
    "WorkItemGoneError",
]


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


class WorkAnnotateError(Exception):
    """The forge write/read for annotation failed — an unreachable forge, a
    rate limit, or an insufficient-scope token."""

    def __init__(self, message: str = "", *, retry_after: datetime | None = None) -> None:
        super().__init__(message)
        #: Set only when the forge's rate budget turned the call away or the forge refused it for rate: when to retry.
        self.retry_after = retry_after


class IWorkAnnotator(Protocol):
    """One configured, credentialed work-source binding's write half."""

    def set_status(self, pointer: WorkRef, marker: WorkStatusMarker) -> None:
        """Set ``pointer``'s marker, exclusively and idempotently — the other
        marker is removed if present."""
        ...

    def clear_status(self, pointer: WorkRef) -> None:
        """Remove every marker from ``pointer``."""
        ...

    def marked_refs(self) -> dict[WorkRef, frozenset[WorkStatusMarker]]:
        """Every ref this binding's forge currently carries a marker on, with the
        *set* of markers each carries, more than one included."""
        ...


class IWorkClosers(Protocol):
    """The close halves of the configured work sources, by source key — all the delivery-time closure pass reads."""

    def closer(self, source: str) -> IWorkCloser | None:
        """The close half of the binding keyed ``source``: always for ``hub``; else ``None`` while
        ``close_forge_writes_enabled`` is false, or when ``source`` names no source or a retired one that no
        longer builds. Raises ``WorkSourceError`` when an active source fails to build."""
        ...

    def label(self, pointer: WorkRef) -> str:
        """``pointer`` as displayed — always its source's current slug, never the stored key."""
        ...


class IWorkAnnotators(Protocol):
    """The write halves of the configured work sources, by source key — all the forge-status pass reads."""

    def annotating_ids(self) -> list[str]:
        """Every active source with an annotator built, by id — the opted-in subset of the configured sources."""
        ...

    def annotator(self, source: str) -> IWorkAnnotator | None:
        """The write half of the binding keyed ``source``, or ``None`` when that source is unconfigured or not opted
        into annotation — the structural "never written to" a non-opted source gets."""
        ...

    def label_clearer(self, source: str) -> IWorkAnnotator | None:
        """The write half of the source keyed ``source`` whether or not it annotates;
        ``None`` when no configured source has that key or it has no forge labels."""
        ...

    def source_label(self, source: str) -> str:
        """The handle the source keyed ``source`` displays under: a configured source's current slug, ``hub`` for the
        built-in one, and ``source`` itself when it names no source."""
        ...
