"""The ``chunks`` terminal-fact predicates (package-private, ``bzh:facts-not-status``).

:func:`chunk_is_terminal` is the one SQL form of "the chunk is stopped or done", mirroring
:meth:`ChunkFacts.status`; the live prefilter :func:`maybe_live` is its negation, the open-question and
open-decision lists and the write fence call it directly, and :func:`settled_stopped` /
:func:`settled_done` split it into its two outcomes. Its one residue: a terminal and a non-terminal
transition at an identical ``(recorded_at, epoch)`` leave the chunk live, since the derivation orders
that tie by fact order and the write path cannot record it."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, or_

from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s


def _done_by_transition(
    chunk_id: ColumnElement[str], *, finished_before: datetime | None = None
) -> ColumnElement[bool]:
    """The chunk named by ``chunk_id`` has a transition into the reserved terminal that is its newest
    movement across transitions, migrations, and restarts. A migration or restart at the same
    ``(recorded_at, epoch)`` supersedes it (the later kind rank wins, as
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.latest_movement` ranks); so does a transition
    that is newer or ties it while not itself terminal. A tie among terminal transitions settles, since
    every order of it derives ``done``. ``finished_before`` further requires that transition to be
    recorded before the instant."""
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
            or_(
                other.c.to_node_id != RESERVED_TERMINAL,
                other.c.recorded_at > terminal.c.recorded_at,
                other.c.epoch > terminal.c.epoch,
            ),
        ),
        exists().where(s.chunk_migrations.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_migrations)),
        exists().where(s.chunk_restarts.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_restarts)),
    )
    clauses = [terminal.c.chunk_id == chunk_id, terminal.c.to_node_id == RESERVED_TERMINAL, ~superseded]
    if finished_before is not None:
        clauses.append(terminal.c.recorded_at < finished_before)
    return exists().where(*clauses)


def chunk_is_terminal(chunk_id: ColumnElement[str]) -> ColumnElement[bool]:
    """The chunk named by ``chunk_id`` — a correlated column or one bound id — is stopped or done: a
    stop, an operator completion, or a terminal transition that is its newest movement. Terminal
    refuses every later write regardless of epoch (``bzh:epoch-fencing``), so this is the derived
    status, not an epoch comparison."""
    return or_(
        exists().where(s.chunk_stopped.c.chunk_id == chunk_id),
        exists().where(s.chunk_completed.c.chunk_id == chunk_id),
        _done_by_transition(chunk_id),
    )


def maybe_live() -> ColumnElement[bool]:
    """Drops every chunk :func:`chunk_is_terminal` settles, and never one
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.status` would derive non-terminal."""
    return ~chunk_is_terminal(s.chunks.c.chunk_id)


def settled_stopped() -> ColumnElement[bool]:
    """The chunks that derive ``stopped``: a stop no operator completion at or after it
    outranks — the ``>=`` tie going to the completion, as
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.status` settles it. Exact."""
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
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.completed_at` falls before that instant.
    Exact, but for the residue :func:`chunk_is_terminal` names."""
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
        _done_by_transition(chunk_id, finished_before=finished_before),
    )
    return or_(and_(*by_completion), by_transition)
