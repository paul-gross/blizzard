"""The ``chunks`` terminal-fact predicates (package-private, ``bzh:facts-not-status``).

Each is sound, never exact: it settles only a chunk :meth:`ChunkFacts.status` would classify
the same way, leaving a same-instant movement tie to the derivation. Together they partition
``chunks`` into settled-stopped, settled-done, and :func:`maybe_live`."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, or_

from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.store import schema as s


def _done_by_transition(*, finished_before: datetime | None = None) -> ColumnElement[bool]:
    """A transition into the reserved terminal that is strictly the newest movement across
    transitions, migrations, and restarts — a tie keeps it unsettled, since
    :meth:`~blizzard.hub.domain.work.ChunkFacts.latest_movement` breaks a tie by kind rank.
    ``finished_before`` further requires that transition to be recorded before the instant."""
    chunk_id = s.chunks.c.chunk_id
    terminal = s.transitions.alias("terminal_transition")
    other = s.transitions.alias("other_transition")

    def not_older(row):  # type: ignore[no-untyped-def]
        return or_(
            row.c.recorded_at > terminal.c.recorded_at,
            and_(row.c.recorded_at == terminal.c.recorded_at, row.c.epoch >= terminal.c.epoch),
        )

    superseded = or_(
        exists().where(
            other.c.chunk_id == terminal.c.chunk_id,
            other.c.transition_id != terminal.c.transition_id,
            not_older(other),
        ),
        exists().where(s.chunk_migrations.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_migrations)),
        exists().where(s.chunk_restarts.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_restarts)),
    )
    clauses = [terminal.c.chunk_id == chunk_id, terminal.c.to_node_id == RESERVED_TERMINAL, ~superseded]
    if finished_before is not None:
        clauses.append(terminal.c.recorded_at < finished_before)
    return exists().where(*clauses)


def maybe_live() -> ColumnElement[bool]:
    """Drops every chunk a terminal fact already settles, and never one
    :meth:`~blizzard.hub.domain.work.ChunkFacts.status` would derive non-terminal. A stop and
    a completion are unconditional — none can be undone — so any chunk carrying either is
    settled by :func:`settled_stopped` or :func:`settled_done`; a terminal transition settles
    only while strictly the newest movement, so a tie keeps the chunk in."""
    chunk_id = s.chunks.c.chunk_id
    return and_(
        ~exists().where(s.chunk_stopped.c.chunk_id == chunk_id),
        ~exists().where(s.chunk_completed.c.chunk_id == chunk_id),
        ~_done_by_transition(),
    )


def settled_stopped() -> ColumnElement[bool]:
    """The chunks that derive ``stopped``: a stop no operator completion at or after it
    outranks — the ``>=`` tie going to the completion, as
    :meth:`~blizzard.hub.domain.work.ChunkFacts.status` settles it. Exact."""
    chunk_id = s.chunks.c.chunk_id
    stop = s.chunk_stopped.alias("settling_stop")
    return exists().where(
        stop.c.chunk_id == chunk_id,
        ~exists().where(
            s.chunk_completed.c.chunk_id == stop.c.chunk_id,
            s.chunk_completed.c.completed_at >= stop.c.stopped_at,
        ),
    )


def settled_done(*, finished_before: datetime | None = None) -> ColumnElement[bool]:
    """The chunks that derive ``done`` — by an operator completion that outranks every stop,
    or, with neither fact present, by an unsuperseded terminal transition. With
    ``finished_before``, only those whose
    :meth:`~blizzard.hub.domain.work.ChunkFacts.completed_at` falls before that instant.
    Exact for the completion branch; the transition branch leaves a tie unsettled."""
    chunk_id = s.chunks.c.chunk_id
    completion = s.chunk_completed.alias("settling_completion")
    by_completion: list[ColumnElement[bool]] = [
        exists().where(
            completion.c.chunk_id == chunk_id,
            ~exists().where(
                s.chunk_stopped.c.chunk_id == completion.c.chunk_id,
                s.chunk_stopped.c.stopped_at > completion.c.completed_at,
            ),
        )
    ]
    if finished_before is not None:
        # ``completed_at`` is the newest completion, so every one must fall before the instant.
        by_completion.append(
            ~exists().where(
                s.chunk_completed.c.chunk_id == chunk_id,
                s.chunk_completed.c.completed_at >= finished_before,
            )
        )
    by_transition = and_(
        ~exists().where(s.chunk_stopped.c.chunk_id == chunk_id),
        ~exists().where(s.chunk_completed.c.chunk_id == chunk_id),
        _done_by_transition(finished_before=finished_before),
    )
    return or_(and_(*by_completion), by_transition)
