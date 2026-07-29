"""The write-half work-source seam — status markers projected onto the forge.

A sibling seam to :mod:`~blizzard.hub.work_sources.source`'s read-only
:class:`~blizzard.hub.work_sources.source.IWorkSource`, not optional methods on it:
``IWorkSource`` is documented as a deliberately read-only pass-through, and optional
methods on a structurally-typed Protocol would force every consumer into ``hasattr``
probing. A sibling Protocol turns "this source may not annotate" into a *presence*
question the registry answers (``IWorkSourceRegistry.annotator``) instead.

Only a per-source, opted-in binding builds one of these (``bzh:dependency-injection``,
the factory) — a non-opted source's registry entry has no annotator at all, so "never
written to" is a property of the object graph rather than a branch someone has to
remember. The reconciler (``blizzard.hub.domain.forge_status.AnnotationReconciler``) is
the sole caller.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from blizzard.hub.domain.work import WorkRef


class WorkStatusMarker(StrEnum):
    """The bucket a live chunk's derived status projects onto the forge.

    Domain vocabulary — the adapter owns rendering this into a vendor-shaped label
    (e.g. GitHub's ``blizzard:ingested`` / ``blizzard:in-progress``).
    """

    INGESTED = "ingested"
    IN_PROGRESS = "in_progress"


class WorkAnnotateError(Exception):
    """The forge write/read for annotation failed — an unreachable forge, a
    rate limit, or an insufficient-scope token. Degrades to a logged skip; never
    raised past the reconciler's sweep."""


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
        *set* of markers each carries — a ref with more than one marker is visible
        to the reconciler's diff as wrong, not as already-correct."""
        ...
