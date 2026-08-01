"""The write-half work-source seam's close capability — delivery-time item closure.

A sibling seam to :mod:`~blizzard.hub.work_sources.annotator`'s own sibling-Protocol
design, for the identical reason that module's docstring already gives: ``IWorkSource``
is a deliberately read-only pass-through, and optional methods on a structurally-typed
Protocol would force every consumer into ``hasattr`` probing. A sibling Protocol turns
"this source may not close" into a *presence* question the registry answers
(``IWorkSourceRegistry.closer``) instead.

Only a per-source, opted-in binding builds one of these (``bzh:dependency-injection``,
the factory) — a non-opted source's registry entry has no closer at all, so "never
closed" is a property of the object graph rather than a branch someone has to
remember. The reconciler (``blizzard.hub.domain.work_closure.DeliveryClosureReconciler``)
is the sole caller.
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.work import WorkRef


class WorkCloseError(Exception):
    """The forge write for closure failed — an unreachable forge, a rate limit, or
    an insufficient-scope token. Degrades to a recorded ``failed`` outcome the
    reconciler retries on its next sweep; never raised past the sweep itself."""


class WorkItemGoneError(WorkCloseError):
    """The work item no longer exists at the source (404/410) — recorded as the
    terminal ``gone`` outcome, unlike the retried ``failed`` outcome a bare
    :class:`WorkCloseError` gets."""


class IWorkCloser(Protocol):
    """One configured, credentialed work-source binding's close half."""

    def close(self, pointer: WorkRef) -> None:
        """Close ``pointer`` at the source, idempotently — closing an
        already-closed item is a clean no-op. Raises :class:`WorkItemGoneError`
        when the item no longer exists, :class:`WorkCloseError` for any other
        failure."""
        ...
