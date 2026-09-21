"""Widens ``ck_finding_facts_kind`` to admit the ``delivered`` fact kind (blizzard#583).

Revision ID: 20260920_1200_finding_delivered_state
Revises: 20260920_1100_hub_usage_harness_provenance
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260920_1200_finding_delivered_state"
down_revision: str | None = "20260920_1100_hub_usage_harness_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FACTS_TABLE = "finding_facts"
_CHECK_NAME = "ck_finding_facts_kind"

_OLD_KINDS = (
    "add",
    "observed",
    "gone",
    "resolved",
    "gone-confirmed",
    "wont-fix",
    "not-a-finding",
    "superseded",
    "reopened",
)
_NEW_KINDS = (
    "add",
    "observed",
    "gone",
    "delivered",
    "resolved",
    "gone-confirmed",
    "wont-fix",
    "not-a-finding",
    "superseded",
    "reopened",
)

# Narrow read/write stub: downgrade backfills only this column.
_facts = sa.Table("finding_facts", sa.MetaData(), sa.Column("kind", sa.String))


def _check_sql(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def _admits_delivered(bind: sa.Connection) -> bool:
    constraints = sa.inspect(bind).get_check_constraints(_FACTS_TABLE)
    return any("'delivered'" in c["sqltext"] for c in constraints)


def upgrade() -> None:
    bind = op.get_bind()
    if _admits_delivered(bind):
        return
    # SQLite has no ALTER for a CHECK constraint, so this table-copy recreate is
    # load-bearing (`blizzard-context:/standards/persistence.md`), not stylistic.
    with op.batch_alter_table(_FACTS_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.create_check_constraint(_CHECK_NAME, _check_sql(_NEW_KINDS))


def downgrade() -> None:
    bind = op.get_bind()
    if not _admits_delivered(bind):
        return
    # `delivered` has no home in the old vocabulary — coalesce to `resolved`, the exit it
    # settles to when nothing intervenes, so narrowing the CHECK never orphans a row.
    bind.execute(_facts.update().where(_facts.c.kind == "delivered").values(kind="resolved"))
    with op.batch_alter_table(_FACTS_TABLE) as batch:
        batch.drop_constraint(_CHECK_NAME, type_="check")
        batch.create_check_constraint(_CHECK_NAME, _check_sql(_OLD_KINDS))
