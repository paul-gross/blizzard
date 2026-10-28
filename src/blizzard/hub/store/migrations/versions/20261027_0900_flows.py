"""flows — each branch's delivery line, the hub-node visits submitted to it, their closures, turns, and facts.

Adds six tenant-keyed tables: ``flows``, ``flow_submissions``, ``flow_submission_flows``, ``flow_submission_closures``,
``hub_turns`` and ``flow_facts``, with their indexes. Writes no row: no submission and no ``landed`` fact for a landing
made before it, since admitting a visit to a flow is the domain's. Downgrade drops the six tables.
Revision ID: 20261027_0900_flows
Revises: 20261025_0900_forges
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261027_0900_flows"
down_revision: str | None = "20261025_0900_forges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
_FLOWS = "flows"
_SUBMISSIONS = "flow_submissions"
_HELD = "flow_submission_flows"
_CLOSURES = "flow_submission_closures"
_TURNS = "hub_turns"
_FACTS = "flow_facts"
#: Indexes this revision adds: name, table, columns.
_INDEXES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ix_flow_submission_flows_tenant_flow_id", _HELD, (_TENANT_COLUMN, "flow_id")),
    ("ix_flow_submission_flows_tenant_submission_id", _HELD, (_TENANT_COLUMN, "submission_id")),
    ("ix_flow_submission_closures_tenant_submission_id", _CLOSURES, (_TENANT_COLUMN, "submission_id")),
    ("ix_hub_turns_tenant_submission_id", _TURNS, (_TENANT_COLUMN, "submission_id")),
    ("ix_hub_turns_tenant_ended_at", _TURNS, (_TENANT_COLUMN, "ended_at")),
    ("ix_flow_facts_tenant_flow_id_id", _FACTS, (_TENANT_COLUMN, "flow_id", "id")),
    ("ix_flow_facts_tenant_submission_id", _FACTS, (_TENANT_COLUMN, "submission_id")),
)


def _has_table(bind: sa.Connection, table: str) -> bool:
    return sa.inspect(bind).has_table(table)


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(i["name"]) for i in sa.inspect(bind).get_indexes(table)}


def _tenant_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([_TENANT_COLUMN], ["tenants.tenant_id"], name=f"fk_{table}_tenant_id")


def _submission_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["submission_id"], [f"{_SUBMISSIONS}.submission_id"], name=f"fk_{table}_submissions"
    )


def _create_flows() -> None:
    op.create_table(
        _FLOWS,
        sa.Column("flow_id", sa.String(), nullable=False),
        sa.Column("resource", sa.String(), nullable=False),
        sa.Column("repository_id", sa.String(), nullable=False),
        sa.Column("branch", sa.String(), nullable=False),
        sa.Column("created_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("flow_id", name="pk_flows"),
        sa.UniqueConstraint(_TENANT_COLUMN, "resource", name="uq_flows_tenant_resource"),
        _tenant_fk(_FLOWS),
    )


def _create_submissions() -> None:
    op.create_table(
        _SUBMISSIONS,
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("node_id", sa.String(), nullable=False),
        sa.Column("epoch", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("arrival", sa.Integer(), nullable=False),
        sa.Column("submitted_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("submission_id", name="pk_flow_submissions"),
        sa.UniqueConstraint(
            _TENANT_COLUMN,
            "chunk_id",
            "node_id",
            "epoch",
            "generation",
            name="uq_flow_submissions_tenant_visit_generation",
        ),
        sa.UniqueConstraint(_TENANT_COLUMN, "arrival", name="uq_flow_submissions_tenant_arrival"),
        sa.ForeignKeyConstraint(["chunk_id"], ["chunks.chunk_id"], name="fk_flow_submissions_chunks"),
        _tenant_fk(_SUBMISSIONS),
    )


def _create_held() -> None:
    op.create_table(
        _HELD,
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("flow_id", sa.String(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("submission_id", "flow_id", name="pk_flow_submission_flows"),
        _submission_fk(_HELD),
        sa.ForeignKeyConstraint(["flow_id"], [f"{_FLOWS}.flow_id"], name="fk_flow_submission_flows_flows"),
        _tenant_fk(_HELD),
    )


def _create_closures() -> None:
    op.create_table(
        _CLOSURES,
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=False),
        sa.Column("closed_at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("submission_id", name="pk_flow_submission_closures"),
        _submission_fk(_CLOSURES),
        _tenant_fk(_CLOSURES),
    )


def _create_turns() -> None:
    op.create_table(
        _TURNS,
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("started_at", UtcDateTime(), nullable=False),
        sa.Column("ended_at", UtcDateTime(), nullable=True),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("turn_id", name="pk_hub_turns"),
        _submission_fk(_TURNS),
        _tenant_fk(_TURNS),
    )


def _create_facts() -> None:
    op.create_table(
        _FACTS,
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("flow_id", sa.String(), nullable=False),
        sa.Column("submission_id", sa.String(), nullable=False),
        sa.Column("chunk_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("revision", sa.String(), nullable=True),
        sa.Column("at", UtcDateTime(), nullable=False),
        sa.Column(_TENANT_COLUMN, sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["flow_id"], [f"{_FLOWS}.flow_id"], name="fk_flow_facts_flows"),
        _submission_fk(_FACTS),
        _tenant_fk(_FACTS),
    )


#: Each table and its creator, parents first.
_TABLES = (
    (_FLOWS, _create_flows),
    (_SUBMISSIONS, _create_submissions),
    (_HELD, _create_held),
    (_CLOSURES, _create_closures),
    (_TURNS, _create_turns),
    (_FACTS, _create_facts),
)


def upgrade() -> None:
    bind = op.get_bind()
    for table, create in _TABLES:
        if not _has_table(bind, table):
            create()
    for name, table, columns in _INDEXES:
        if name not in _index_names(bind, table):
            op.create_index(name, table, list(columns))


def downgrade() -> None:
    bind = op.get_bind()
    for table, _ in reversed(_TABLES):
        if _has_table(bind, table):
            op.drop_table(table)
