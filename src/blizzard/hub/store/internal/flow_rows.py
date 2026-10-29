"""Package-private SQL helpers for the flow record, shared by every store transaction that ends a turn, closes a
submission, or records a landing — the way ``chunk_rows`` serves the chunk stores.

Each helper takes the writer's own ``Connection`` and recovers its builders with :func:`statements_of`, so the
connection must come from a scoped ``write`` or a locked handle over one. Facts only (``bzh:facts-not-status``): a
submission is open while it has no closure, a turn while it has no ``ended_at``."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Row

from blizzard.foundation.event_log import EVENT_LOG_SEVERITY
from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.delivery.flows import Flow, FlowFactKind, FlowLanding, TurnEvent, Withdrawal
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import statements_of

_sub = s.flow_submissions
_held = s.flow_submission_flows
_closures = s.flow_submission_closures
_turns = s.hub_turns


def flow_of(row: Row[Any]) -> Flow:
    return Flow(flow_id=row.flow_id, repository_id=row.repository_id, branch=row.branch, created_at=row.created_at)


def flows_held(conn: Connection, submission_ids: Sequence[str]) -> dict[str, tuple[Flow, ...]]:
    """The flows each submission holds, by submission id, each in resource order."""
    stmts = statements_of(conn)
    held: dict[str, list[Flow]] = {}
    joined = stmts.scope_join(_held, s.flows, _held.c.flow_id == s.flows.c.flow_id)
    for batch in id_batches(list(submission_ids)):
        rows = conn.execute(
            stmts.select(joined, _held.c.submission_id, *s.flows.c)
            .where(_held.c.submission_id.in_(batch))
            .order_by(_held.c.submission_id, s.flows.c.resource)
        ).all()
        for row in rows:
            held.setdefault(row.submission_id, []).append(flow_of(row))
    return {submission_id: tuple(flows) for submission_id, flows in held.items()}


def held_by(conn: Connection, submission_id: str) -> tuple[str, tuple[Flow, ...]]:
    """The submission's chunk and the flows it holds."""
    chunk_id = conn.execute(
        statements_of(conn).select(_sub, _sub.c.chunk_id).where(_sub.c.submission_id == submission_id)
    ).scalar_one()
    return chunk_id, flows_held(conn, [submission_id]).get(submission_id, ())


def write_facts(
    conn: Connection,
    flows: Sequence[Flow],
    submission_id: str,
    chunk_id: str,
    *,
    kind: FlowFactKind,
    outcome: str | None,
    at: datetime,
    revision: str | None = None,
) -> None:
    """One fact per flow, so each flow's record stands alone."""
    stmts = statements_of(conn)
    for flow in flows:
        conn.execute(
            stmts.insert(s.flow_facts).values(
                flow_id=flow.flow_id,
                submission_id=submission_id,
                chunk_id=chunk_id,
                kind=kind,
                outcome=outcome,
                revision=revision,
                at=at,
            )
        )


def write_event(conn: Connection, event: TurnEvent, *, at: datetime) -> int:
    """``event`` as one row of the tenant's event log, in the writer's transaction; answers the row's id."""
    result = conn.execute(
        statements_of(conn)
        .insert(s.event_log)
        .values(
            severity=EVENT_LOG_SEVERITY[event.kind],
            kind=event.kind,
            runner_id=None,
            chunk_id=event.chunk_id,
            lease_id=None,
            node_name=event.node_name,
            message=event.message,
            detail=json.dumps(dict(event.detail)),
            recorded_at=at,
        )
    )
    key = result.inserted_primary_key
    assert key is not None
    return int(key[0])


def close_submission(conn: Connection, submission_id: str, *, outcome: str, at: datetime) -> bool:
    """Insert the closure and its ``withdrawn`` facts only when no closure exists — the first writer wins. ``outcome``
    is a verdict's choice or a :data:`Withdrawal`."""
    stmts = statements_of(conn)
    closed = conn.execute(
        stmts.select(_closures, _closures.c.submission_id).where(_closures.c.submission_id == submission_id)
    ).first()
    if closed is not None:
        return False
    conn.execute(stmts.insert(_closures).values(submission_id=submission_id, outcome=outcome, closed_at=at))
    chunk_id, flows = held_by(conn, submission_id)
    write_facts(conn, flows, submission_id, chunk_id, kind="withdrawn", outcome=outcome, at=at)
    return True


def has_open_turn(conn: Connection, submission_id: str) -> bool:
    stmts = statements_of(conn)
    return (
        conn.execute(
            stmts.select(_turns, _turns.c.turn_id).where(
                _turns.c.submission_id == submission_id, _turns.c.ended_at.is_(None)
            )
        ).first()
        is not None
    )


def end_turn(conn: Connection, turn_id: str, *, outcome: str, at: datetime) -> str | None:
    """End the open turn ``outcome`` with a ``turn-ended`` fact per flow its submission holds, answering the
    submission's id — or ``None``, writing nothing, when the turn has already ended."""
    stmts = statements_of(conn)
    turn = conn.execute(
        stmts.select(_turns, _turns.c.submission_id).where(_turns.c.turn_id == turn_id, _turns.c.ended_at.is_(None))
    ).first()
    if turn is None:
        return None
    conn.execute(stmts.update(_turns).where(_turns.c.turn_id == turn_id).values(ended_at=at, outcome=outcome))
    chunk_id, flows = held_by(conn, turn.submission_id)
    write_facts(conn, flows, turn.submission_id, chunk_id, kind="turn-ended", outcome=outcome, at=at)
    return str(turn.submission_id)


def withdraw_waiting(conn: Connection, chunk_id: str, *, outcome: Withdrawal, at: datetime) -> int:
    """Close ``outcome`` every open submission of the chunk that has no open turn — a lever's withdrawal. One with an
    open turn is left to that turn's end, which asks ``withdrawal_for`` and closes it the same way. Answers how many
    it closed."""
    stmts = statements_of(conn)
    open_ids = conn.execute(
        stmts.select(_sub, _sub.c.submission_id)
        .where(
            _sub.c.chunk_id == chunk_id,
            ~stmts.exists(_closures, _closures.c.submission_id == _sub.c.submission_id),
            ~stmts.exists(_turns, _turns.c.submission_id == _sub.c.submission_id, _turns.c.ended_at.is_(None)),
        )
        .order_by(_sub.c.arrival)
    ).scalars()
    return sum(close_submission(conn, submission_id, outcome=outcome, at=at) for submission_id in list(open_ids))


def write_landing(conn: Connection, landing: FlowLanding, *, revision: str, at: datetime) -> int:
    """The ``landed`` fact on the landing's flow at ``revision``, and its ``flow-landed`` event row, whose id it
    answers."""
    write_facts(
        conn,
        [landing.flow],
        landing.submission_id,
        landing.chunk_id,
        kind="landed",
        outcome=None,
        revision=revision,
        at=at,
    )
    return write_event(conn, landing.event, at=at)
