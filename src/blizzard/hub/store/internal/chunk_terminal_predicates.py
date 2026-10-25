"""The ``chunks`` terminal-fact predicates (package-private, ``bzh:facts-not-status``).

:func:`chunk_is_terminal` is the one SQL form of "the chunk is stopped or done", mirroring
:meth:`ChunkFacts.status`; the live prefilter :func:`maybe_live` is its negation, the open-question and
open-decision lists and the write fence call it directly, and :func:`settled_stopped` /
:func:`settled_done` split it into its two outcomes; :func:`done_completed_at` is the instant a chunk
:func:`settled_done` settles finished, mirroring :meth:`ChunkFacts.completed_at`. Its one residue: a terminal and a
non-terminal transition at an identical ``(recorded_at, epoch)`` leave the chunk live, since the derivation orders
that tie by fact order and the write path cannot record it.

Each predicate takes the caller's :class:`ScopedStatements`, and every ``EXISTS`` it nests is built
through them — each correlated table and alias carries the scope's tenant on its own, so a predicate
never reads another tenant's facts whatever the enclosing read selects from."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, and_, func, or_

from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedStatements


def _done_by_transition(
    stmts: ScopedStatements, chunk_id: ColumnElement[str], *, finished_before: datetime | None = None
) -> ColumnElement[bool]:
    """The chunk named by ``chunk_id`` has a transition into the reserved terminal that is its newest
    movement across transitions, migrations, and restarts. Supersession follows
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.latest_movement`; a transition
    that is newer or ties it while not itself terminal. A tie among terminal transitions settles, since
    every order of it derives ``done``. ``finished_before`` further requires that transition to be
    recorded before the instant."""
    terminal = s.transitions.alias("terminal_transition")
    clauses = [terminal.c.chunk_id == chunk_id, *_unsuperseded_terminal(stmts, terminal)]
    if finished_before is not None:
        clauses.append(terminal.c.recorded_at < finished_before)
    return stmts.exists(terminal, *clauses)


def _unsuperseded_terminal(stmts: ScopedStatements, terminal: Any) -> list[ColumnElement[bool]]:
    """The criteria making the ``terminal`` transition alias a transition into the reserved terminal
    that no later movement supersedes — :func:`_done_by_transition`'s own test, factored out so the
    completion instant ranks exactly the transitions that settle."""
    other = s.transitions.alias("other_transition")

    def not_older(row):
        return or_(
            row.c.recorded_at > terminal.c.recorded_at,
            and_(row.c.recorded_at == terminal.c.recorded_at, row.c.epoch >= terminal.c.epoch),
        )

    superseded = or_(
        stmts.exists(
            other,
            other.c.chunk_id == terminal.c.chunk_id,
            not_older(other),
            or_(other.c.to_node_id != RESERVED_TERMINAL, other.c.recorded_at > terminal.c.recorded_at),
        ),
        stmts.exists(
            s.chunk_migrations, s.chunk_migrations.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_migrations)
        ),
        stmts.exists(s.chunk_restarts, s.chunk_restarts.c.chunk_id == terminal.c.chunk_id, not_older(s.chunk_restarts)),
    )
    return [terminal.c.to_node_id == RESERVED_TERMINAL, ~superseded]


def chunk_is_terminal(stmts: ScopedStatements, chunk_id: ColumnElement[str]) -> ColumnElement[bool]:
    """The chunk named by ``chunk_id`` — a correlated column or one bound id — is stopped or done: a
    stop, an operator completion, or a terminal transition that is its newest movement. Terminal
    refuses every later write regardless of epoch (``bzh:epoch-fencing``), so this is the derived
    status, not an epoch comparison."""
    return or_(
        stmts.exists(s.chunk_stopped, s.chunk_stopped.c.chunk_id == chunk_id),
        stmts.exists(s.chunk_completed, s.chunk_completed.c.chunk_id == chunk_id),
        _done_by_transition(stmts, chunk_id),
    )


def maybe_live(stmts: ScopedStatements) -> ColumnElement[bool]:
    """Drops every chunk :func:`chunk_is_terminal` settles, and never one
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.status` would derive non-terminal."""
    return ~chunk_is_terminal(stmts, s.chunks.c.chunk_id)


def settled_stopped(stmts: ScopedStatements) -> ColumnElement[bool]:
    """The chunks that derive ``stopped``: a stop no operator completion at or after it
    outranks — the ``>=`` tie going to the completion, as
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.status` settles it. Exact."""
    chunk_id = s.chunks.c.chunk_id
    stop = s.chunk_stopped.alias("settling_stop")
    return stmts.exists(
        stop,
        stop.c.chunk_id == chunk_id,
        ~stmts.exists(
            s.chunk_completed,
            s.chunk_completed.c.chunk_id == stop.c.chunk_id,
            s.chunk_completed.c.completed_at >= stop.c.stopped_at,
        ),
    )


def settled_done(stmts: ScopedStatements, *, finished_before: datetime | None = None) -> ColumnElement[bool]:
    """The chunks that derive ``done`` — by an operator completion that outranks every stop,
    or, with neither fact present, by an unsuperseded terminal transition. With
    ``finished_before``, only those whose
    :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.completed_at` falls before that instant.
    Exact, but for the residue :func:`chunk_is_terminal` names."""
    chunk_id = s.chunks.c.chunk_id
    completion = s.chunk_completed.alias("settling_completion")
    by_completion: list[ColumnElement[bool]] = [
        stmts.exists(
            completion,
            completion.c.chunk_id == chunk_id,
            ~stmts.exists(
                s.chunk_stopped,
                s.chunk_stopped.c.chunk_id == completion.c.chunk_id,
                s.chunk_stopped.c.stopped_at > completion.c.completed_at,
            ),
        )
    ]
    if finished_before is not None:
        # ``completed_at`` is the newest completion, so every one must fall before the instant.
        by_completion.append(
            ~stmts.exists(
                s.chunk_completed,
                s.chunk_completed.c.chunk_id == chunk_id,
                s.chunk_completed.c.completed_at >= finished_before,
            )
        )
    by_transition = and_(
        ~stmts.exists(s.chunk_stopped, s.chunk_stopped.c.chunk_id == chunk_id),
        ~stmts.exists(s.chunk_completed, s.chunk_completed.c.chunk_id == chunk_id),
        _done_by_transition(stmts, chunk_id, finished_before=finished_before),
    )
    return or_(and_(*by_completion), by_transition)


def done_completed_at(stmts: ScopedStatements) -> ColumnElement[datetime]:
    """The instant a chunk :func:`settled_done` settles finished, correlated to ``chunks`` — the SQL form
    of :meth:`~blizzard.hub.domain.chunk.model.ChunkFacts.completed_at` for a ``done`` chunk: its
    newest operator completion, else the recorded instant of its unsuperseded terminal transition.
    ``NULL`` for a chunk :func:`settled_done` does not settle; rank only chunks it does."""
    chunk_id = s.chunks.c.chunk_id
    terminal = s.transitions.alias("ranked_terminal")
    completion = stmts.select(s.chunk_completed, func.max(s.chunk_completed.c.completed_at)).where(
        s.chunk_completed.c.chunk_id == chunk_id
    )
    transition = stmts.select(terminal, func.max(terminal.c.recorded_at)).where(
        terminal.c.chunk_id == chunk_id, *_unsuperseded_terminal(stmts, terminal)
    )
    return func.coalesce(completion.scalar_subquery(), transition.scalar_subquery())
