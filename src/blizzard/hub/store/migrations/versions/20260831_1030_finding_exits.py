"""Human-driven exit verbs (blizzard#394 Phase 1): widens the ``ck_finding_facts_kind``
CHECK constraint from 3 kinds to the 9 in ``FACT_KINDS`` (``domain/findings.py``), adds
``finding_facts.actor``/``proposal_id``/``superseded_by``, and adds
``findings.introduced_at`` — nullable, never backfilled (D5): null wherever unresolved,
by design.

This tree's first CHECK-constraint widen (a Tested assumption worth flagging): SQLite
exposes no ALTER for a CHECK constraint at all, so ``batch_alter_table``'s table-copy
recreate is load-bearing here, not merely a style choice — the same recreate is what lets
``proposal_id``/``superseded_by`` carry a real ``ForeignKey`` despite SQLite's inability to
ALTER-add or ALTER-drop a constrained column outside a full rebuild (see
``20260830_2015_garden_proposals_source_artifact.py``'s and
``20260721_1500_cli_auth_state_user.py``'s docstrings for the plain-``add_column`` cases
that route around it instead).

Revision ID: 20260831_1030_finding_exits
Revises: 20260831_0945_garden_proposal_closures
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260831_1030_finding_exits"
down_revision: str | None = "20260831_0945_garden_proposal_closures"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FACTS_TABLE = "finding_facts"
_FINDINGS_TABLE = "findings"
_CHECK_NAME = "ck_finding_facts_kind"
_INTRODUCED_AT_COLUMN = "introduced_at"

_PROPOSAL_ID_FK = "fk_finding_facts_proposal_id_garden_proposals"
_SUPERSEDED_BY_FK = "fk_finding_facts_superseded_by_findings"

_OLD_KINDS = ("add", "observed", "gone")
_NEW_KINDS = (
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


def _check_sql(kinds: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{kind}'" for kind in kinds) + ")"


def _facts_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_FACTS_TABLE)}


def _findings_columns(bind: sa.Connection) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(_FINDINGS_TABLE)}


def upgrade() -> None:
    bind = op.get_bind()
    if "actor" not in _facts_columns(bind):
        with op.batch_alter_table(_FACTS_TABLE) as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
            batch.add_column(sa.Column("actor", sa.String(), nullable=True))
            batch.add_column(
                sa.Column(
                    "proposal_id",
                    sa.String(),
                    sa.ForeignKey("garden_proposals.proposal_id", name=_PROPOSAL_ID_FK),
                    nullable=True,
                )
            )
            batch.add_column(
                sa.Column(
                    "superseded_by",
                    sa.String(),
                    sa.ForeignKey("findings.finding_id", name=_SUPERSEDED_BY_FK),
                    nullable=True,
                )
            )
            batch.create_check_constraint(_CHECK_NAME, _check_sql(_NEW_KINDS))
    if _INTRODUCED_AT_COLUMN not in _findings_columns(bind):
        op.add_column(_FINDINGS_TABLE, sa.Column(_INTRODUCED_AT_COLUMN, sa.DateTime(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if _INTRODUCED_AT_COLUMN in _findings_columns(bind):
        op.drop_column(_FINDINGS_TABLE, _INTRODUCED_AT_COLUMN)
    if "actor" in _facts_columns(bind):
        with op.batch_alter_table(_FACTS_TABLE) as batch:
            batch.drop_constraint(_CHECK_NAME, type_="check")
            batch.drop_column("superseded_by")
            batch.drop_column("proposal_id")
            batch.drop_column("actor")
            batch.create_check_constraint(_CHECK_NAME, _check_sql(_OLD_KINDS))
