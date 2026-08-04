"""The operator requeue — ``blizzard runner requeue <chunk-id>`` (issue #53).

Behind ``POST /chunks/{id}/requeues``: the explicit hand-back after a human worked a
needs_human chunk interactively, whether by ``blizzard runner takeover`` or by pasting
an escalation's resume command with no recorded takeover at all. Both routes leave the
same local shape — a lease closed ``escalated`` with no later mint
(:meth:`~blizzard.runner.store.repository.IReadRunnerStore.open_escalation_for_chunk`) —
so one read covers both.

:meth:`RequeueService.requeue` only appends the clearing fact (``bzh:crash-correctness``
— fact first, no direct spawn from the API edge); the next FILL tick reads it back and
spawns the fresh attempt. The chunk's route is never released and it never re-enters the
hub's queue — this is the narrower, same-runner, same-place hand-back a human takeover
implies (contrast ``blizzard hub requeue``,
:class:`blizzard.hub.domain.decisions.RequeueService`).

The retry budget is **carried, not reset**: the human's requeue buys the chunk exactly
one more try, not a fresh budget.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.runner.store.repository import IWriteRunnerStore

__all__ = ["ChunkNotRequeueable", "RequeueBlockedByOpenTakeover", "RequeueService"]


class RequeueError(Exception):
    """Base for the requeue domain's refusals — the API edge maps these to ``409``."""


class RequeueBlockedByOpenTakeover(RequeueError):
    """The chunk's takeover is still open — the human's interactive session holds it."""


class ChunkNotRequeueable(RequeueError):
    """The chunk carries no open escalation — nothing needs_human to clear."""


class RequeueService:
    """Composition-root-wired: the store and clock (issue #53)."""

    def __init__(self, store: IWriteRunnerStore, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def requeue(self, chunk_id: str) -> None:
        """Clear ``chunk_id``'s local needs_human hold, or raise a ``409``-mapped refusal.

        Checked in order: an **open takeover** refuses first — the human's interactive
        session, if still alive, must end before anything else touches the chunk
        (one-process-per-session); then the chunk must carry an **open escalation** — the
        needs_human shape this verb exists to clear, whether reached directly or by way of
        an ended takeover."""
        if self._store.open_takeover_for_chunk(chunk_id) is not None:
            raise RequeueBlockedByOpenTakeover(
                f"chunk {chunk_id} has an open takeover — end the interactive session before requeuing"
            )
        if self._store.open_escalation_for_chunk(chunk_id) is None:
            raise ChunkNotRequeueable(f"chunk {chunk_id} is not needs_human — nothing to requeue")
        self._store.record_requeue(chunk_id=chunk_id, at=self._clock.now())
