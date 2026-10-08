"""tenant_id backfill — every tenant-owned table gains a nullable ``tenant_id``, filled with the store's only tenant.

Each table in :data:`_KEYED` gains ``tenant_id``, nullable and appended last, in a batch of its own under
``recreate="auto"``: SQLite and Postgres both add the column in place. Every row is then stamped with the one tenant
``20261008_1100_tenants`` inserted, in bounded batches chosen by primary key. ``NOT NULL``, the foreign key to
``tenants``, and the rebuilt keys and indexes come in a later revision. A table created already keyed (the invitation
tables) has the column and no null row, so both steps pass over it.

The upgrade refuses, before touching any table, unless the store holds exactly one tenant. Re-running it skips a table
whose column is already there and stamps only rows still null. The downgrade refuses while more than one tenant exists,
naming them; with one it drops the column from every table this revision added it to.
Revision ID: 20261008_1200_tenant_id_backfill
Revises: 20261008_1100_tenants
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261008_1200_tenant_id_backfill"
down_revision: str | None = "20261008_1100_tenants"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_COLUMN = "tenant_id"
_BATCH_ROWS = 1000

# Every table outside the global list as the store held it at this revision, with the primary-key columns the backfill
# pages by — frozen here, never derived from ``schema.metadata`` (``bzh:keyed-table-migration``).
# --- M-05 begin ---
_KEYED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("answer_deliveries", ("id",)),
    ("artifacts", ("artifact_id",)),
    ("chunk_bounces", ("id",)),
    ("chunk_completed", ("id",)),
    ("chunk_deleted", ("id",)),
    ("chunk_dependencies", ("dependency_id",)),
    ("chunk_grouped", ("id",)),
    ("chunk_migrations", ("migration_id",)),
    ("chunk_pause_facts", ("id",)),
    ("chunk_promoted", ("id",)),
    ("chunk_restarts", ("id",)),
    ("chunk_stopped", ("id",)),
    ("chunk_work_refs", ("id",)),
    ("chunks", ("chunk_id",)),
    ("close_intent_attempts", ("id",)),
    ("close_intents", ("id",)),
    ("config_changes", ("id",)),
    ("decision_resolutions", ("decision_id",)),
    ("decisions", ("decision_id",)),
    ("delivery_landed", ("id",)),
    ("delivery_repo_landed", ("id",)),
    ("epoch_owners", ("id",)),
    ("escalations", ("id",)),
    ("event_log", ("id",)),
    ("finding_facts", ("id",)),
    ("finding_sets", ("finding_set_id",)),
    ("findings", ("finding_id",)),
    ("forge_annotation_facts", ("id",)),
    ("garden_proposal_closures", ("id",)),
    ("garden_proposal_findings", ("proposal_id", "finding_id")),
    ("garden_proposals", ("proposal_id",)),
    ("graph_artifacts", ("graph_id", "name")),
    ("graph_choices", ("choice_id",)),
    ("graph_edges", ("edge_id",)),
    ("graph_lifecycle_facts", ("id",)),
    ("graph_nodes", ("node_id",)),
    ("graph_policy_facts", ("id",)),
    ("graph_sessions", ("graph_id", "name")),
    ("graphs", ("graph_id",)),
    ("hub_exec_slot", ("slot_id",)),
    ("hub_node_poll", ("id",)),
    ("invitation_facts", ("id",)),
    ("invitations", ("invitation_id",)),
    ("keyed_locks", ("namespace", "key")),
    ("lease_facts", ("id",)),
    ("question_answers", ("question_id",)),
    ("questions", ("question_id",)),
    ("queue_positions", ("id",)),
    ("repositories", ("name",)),
    ("repository_lifecycle_facts", ("id",)),
    ("requeues", ("id",)),
    ("route_created", ("route_id",)),
    ("route_environments", ("id",)),
    ("route_released", ("id",)),
    ("route_token_minted", ("id",)),
    ("routine_lifecycle_facts", ("id",)),
    ("routine_scopes", ("routine_id", "scope_slug")),
    ("routines", ("routine_id",)),
    ("runner_external_usage", ("runner_id", "slug")),
    ("runner_external_usage_misses", ("runner_id", "slug")),
    ("runner_high_water", ("runner_id",)),
    ("runner_lifecycle_facts", ("id",)),
    ("runner_local_pause_facts", ("id",)),
    ("runner_pause_facts", ("id",)),
    ("runner_registrations", ("runner_id",)),
    ("runner_token_revocations", ("id",)),
    ("scope_lifecycle_facts", ("id",)),
    ("scopes", ("slug",)),
    ("secret_lifecycle_facts", ("id",)),
    ("secrets", ("name",)),
    ("transcript_event_derivations", ("segment_id", "extractor_version")),
    ("transcript_event_drops", ("id",)),
    ("transcript_events", ("id",)),
    ("transcript_high_water", ("runner_id",)),
    ("transcript_segments", ("id",)),
    ("transitions", ("transition_id",)),
    ("usage_facts", ("id",)),
    ("work_item_closures", ("id",)),
    ("work_item_runs", ("work_item_id",)),
    ("work_item_sequence", ("source",)),
    ("work_items", ("work_item_id",)),
    ("work_source_lifecycle_facts", ("id",)),
    ("work_sources", ("name",)),
)
# --- M-05 end ---

# The tables 20261008_1100_tenants created with ``tenant_id`` already in place; the downgrade leaves their column.
_CREATED_KEYED = frozenset({"invitations", "invitation_facts"})

_tenants = sa.table("tenants", sa.column("tenant_id", sa.String))


def _stub(table: str, primary_key: tuple[str, ...]) -> sa.TableClause:
    """A narrow stub: the primary key the backfill pages by, and the column it fills."""
    return sa.table(table, *(sa.column(name) for name in primary_key), sa.column(_TENANT_COLUMN, sa.String))


def _has_column(bind: sa.Connection, table: str) -> bool:
    return any(column["name"] == _TENANT_COLUMN for column in sa.inspect(bind).get_columns(table))


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _only_tenant(bind: sa.Connection) -> str:
    tenants = _tenant_ids(bind)
    if len(tenants) != 1:
        held = ", ".join(tenants) if tenants else "none"
        raise RuntimeError(
            f"the tenant_id backfill carries every row into the store's only tenant, but the store holds "
            f"{len(tenants)} tenants ({held}); it needs exactly one"
        )
    return tenants[0]


def _add_column(bind: sa.Connection, table: str) -> None:
    if _has_column(bind, table):
        return
    with op.batch_alter_table(table, recreate="auto") as batch:
        batch.add_column(sa.Column(_TENANT_COLUMN, sa.String(), nullable=True))


def _backfill(bind: sa.Connection, table: str, primary_key: tuple[str, ...], tenant_id: str) -> None:
    """Stamp the rows still null, at most ``_BATCH_ROWS`` per statement."""
    stub = _stub(table, primary_key)
    columns = [stub.c[name] for name in primary_key]
    key = sa.tuple_(*columns) if len(columns) > 1 else columns[0]
    page = sa.select(*columns).where(stub.c[_TENANT_COLUMN].is_(None)).limit(_BATCH_ROWS)
    while rows := [tuple(row) if len(columns) > 1 else row[0] for row in bind.execute(page)]:
        bind.execute(sa.update(stub).where(key.in_(rows)).values({_TENANT_COLUMN: tenant_id}))


def upgrade() -> None:
    bind = op.get_bind()
    tenant_id = _only_tenant(bind)
    for table, primary_key in _KEYED:
        _add_column(bind, table)
        _backfill(bind, table, primary_key, tenant_id)


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    for table, _ in reversed(_KEYED):
        if table in _CREATED_KEYED or not _has_column(bind, table):
            continue
        with op.batch_alter_table(table, recreate="auto") as batch:
            batch.drop_column(_TENANT_COLUMN)
