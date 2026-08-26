"""Back-fill ``close_intents`` for every already-landed or hand-completed, non-ephemeral
chunk whose refs carry no terminal ``work_item_closures`` outcome (D7) — the
source-agnostic set ``closable_work_refs()`` itself named, stamped at the landing or
completion instant that made each chunk closable.

Because no deployment ever set ``close = true`` (a later revision removes the key), this
back-fill closes the whole accumulated delivered backlog in one pass on first drain — the
repair blizzard#383 exists to make, not a side effect.

Revision ID: 20260826_0930_close_intents_backfill
Revises: 20260826_0900_close_intents
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy import insert, select

from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.store.schema import close_intents

revision: str = "20260826_0930_close_intents_backfill"
down_revision: str | None = "20260826_0900_close_intents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Read-only references, not creates — the frozen shape is the narrow stub of columns
# this revision's selects name (``bzh:frozen-revisions``). Never created, never dropped.
_frozen_metadata = sa.MetaData()

chunk_work_refs = sa.Table(
    "chunk_work_refs",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
)
chunk_grouped = sa.Table(
    "chunk_grouped",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
)
chunk_deleted = sa.Table(
    "chunk_deleted",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
)
delivery_landed = sa.Table(
    "delivery_landed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("landed_at", UtcDateTime, nullable=False),
)
delivery_repo_landed = sa.Table(
    "delivery_repo_landed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("landed_at", UtcDateTime, nullable=False),
)
chunk_completed = sa.Table(
    "chunk_completed",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("completed_at", UtcDateTime, nullable=False),
)
artifacts = sa.Table(
    "artifacts",
    _frozen_metadata,
    sa.Column("artifact_id", sa.String, primary_key=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("name", sa.String, nullable=False),
    sa.Column("produced_at", UtcDateTime, nullable=False),
)
work_item_closures = sa.Table(
    "work_item_closures",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("chunk_id", sa.String, nullable=False),
    sa.Column("source", sa.String, nullable=False),
    sa.Column("ref", sa.String, nullable=False),
    sa.Column("outcome", sa.String, nullable=False),
)

_TERMINAL_OUTCOMES = ("closed", "gone")
_MARKER_PREFIX = "merged/"


def _earliest(existing: datetime | None, candidate: datetime) -> datetime:
    return candidate if existing is None or candidate < existing else existing


def upgrade() -> None:
    bind = op.get_bind()

    ephemeral = {r.chunk_id for r in bind.execute(select(chunk_grouped.c.chunk_id))} | {
        r.chunk_id for r in bind.execute(select(chunk_deleted.c.chunk_id))
    }

    closable_at: dict[str, datetime] = {}
    for r in bind.execute(select(delivery_landed.c.chunk_id, delivery_landed.c.landed_at)):
        closable_at[r.chunk_id] = _earliest(closable_at.get(r.chunk_id), r.landed_at)
    for r in bind.execute(select(delivery_repo_landed.c.chunk_id, delivery_repo_landed.c.landed_at)):
        closable_at[r.chunk_id] = _earliest(closable_at.get(r.chunk_id), r.landed_at)
    for r in bind.execute(
        select(artifacts.c.chunk_id, artifacts.c.produced_at).where(artifacts.c.name.like(f"{_MARKER_PREFIX}%"))
    ):
        closable_at[r.chunk_id] = _earliest(closable_at.get(r.chunk_id), r.produced_at)
    for r in bind.execute(select(chunk_completed.c.chunk_id, chunk_completed.c.completed_at)):
        closable_at[r.chunk_id] = _earliest(closable_at.get(r.chunk_id), r.completed_at)

    terminal = {
        (r.chunk_id, r.source, r.ref)
        for r in bind.execute(
            select(work_item_closures.c.chunk_id, work_item_closures.c.source, work_item_closures.c.ref).where(
                work_item_closures.c.outcome.in_(_TERMINAL_OUTCOMES)
            )
        )
    }
    already_enqueued = {
        (r.chunk_id, r.source, r.ref)
        for r in bind.execute(select(close_intents.c.chunk_id, close_intents.c.source, close_intents.c.ref))
    }

    rows = []
    for r in bind.execute(select(chunk_work_refs.c.chunk_id, chunk_work_refs.c.source, chunk_work_refs.c.ref)):
        if r.chunk_id in ephemeral:
            continue
        at = closable_at.get(r.chunk_id)
        if at is None:
            continue
        key = (r.chunk_id, r.source, r.ref)
        if key in terminal or key in already_enqueued:
            continue
        already_enqueued.add(key)  # guard a duplicate chunk_work_refs row within this same pass
        rows.append({"chunk_id": r.chunk_id, "source": r.source, "ref": r.ref, "enqueued_at": at, "retired_at": None})

    if rows:
        bind.execute(insert(close_intents), rows)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(close_intents.delete())
