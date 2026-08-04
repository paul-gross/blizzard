"""Chunk promotion — flip a not-ready chunk to ready.

The other half of the readiness lifecycle :mod:`blizzard.hub.domain.ingest` opens: ingest
mints a chunk in a NOT-READY resting state (visible on the board, never claimed), and
``POST /chunks/{id}/promote`` appends the ``chunk.promoted`` fact that flips it to ``ready``
so a runner may claim it on a subsequent tick. Facts append, status derives
(``bzh:facts-not-status``): promotion is one fact, and readiness is re-derived from it.

Holds the *write* chunk repository (``bzh:controller-read-only``) — which is also a
:class:`~blizzard.hub.domain.work.IReadChunkRepository`, so no separate read seam is
needed to check whether a chunk is already promoted before writing anything.

Promotion also stamps an explicit tail queue position (issue #137). That is two writes,
not one — ``record_promote`` then ``record_queue_position`` — so the service guards the
whole op on the chunk's *current* promoted-ness (read first, via
:meth:`~blizzard.hub.domain.work.IReadChunkRepository.load_facts`) rather than relying on
``record_promote``'s own idempotency: a repeated promote (a double board click, a CLI
retry) must not keep shoving an already-ready chunk to the back of the queue (pinned by
tests/test_promote_service.py::test_promote_is_a_complete_no_op_on_an_already_promoted_chunk).

Crash safety for the two-write op itself (``bzh:crash-correctness``): a crash between the
two writes leaves the ``chunk.promoted`` fact recorded with no explicit position, which
self-heals —
:meth:`~blizzard.hub.domain.queue.QueueService._effective_position`'s fallback sorts by
``chunk_promoted.promoted_at`` and still lands the chunk at the tail (pinned by
tests/test_queue_service.py::test_promoted_but_unmoved_chunk_falls_back_to_promoted_at_not_minted_at).
See ``blizzard-context:/architecture/crash-correctness.md``'s recorded exemptions.
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.queue import QueueService
from blizzard.hub.domain.work import IWriteChunkRepository


class PromoteService:
    """Promote a not-ready chunk to ready — ``blizzard hub promote``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def promote(self, chunk_id: str) -> int | None:
        """Append the ``chunk.promoted`` fact and stamp an explicit tail queue position.

        A complete no-op — no fact, no position write — if the chunk is already
        promoted (see the module docstring for why this can't lean on
        ``record_promote``'s own idempotency alone). Otherwise records the promote fact,
        then stamps ``max(effective positions of currently-ready chunks) + 1.0`` (or
        ``0.0`` if none are ready) as the chunk's explicit position, so it lands at the
        tail regardless of ``minted_at``. The ready set and its positions are read
        *before* either write, so the chunk being promoted never counts itself.

        Returns the freshly-written ``chunk_promoted.id`` (issue #213's activity-feed
        key), or ``None`` on the already-promoted no-op — there is no fresh row to name."""
        facts = self._chunks.load_facts(chunk_id)
        if facts is not None and facts.promoted:
            return None
        ready = self._chunks.list_ready()
        if ready:
            positions = self._chunks.queue_positions()
            promoted_ats = self._chunks.promoted_ats()
            tail = max(QueueService._effective_position(c, positions, promoted_ats) for c in ready) + 1.0
        else:
            tail = 0.0
        at = self._clock.now()
        promoted_id = self._chunks.record_promote(chunk_id, at=at)
        self._chunks.record_queue_position(chunk_id, position=tail, at=at)
        return promoted_id
