"""Add the hosted runners table and its append-only facts.

Revision ID: 20261119_1000_hosted_runners
Revises: 20261118_1000_runner_hub_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261119_1000_hosted_runners"
down_revision: str | None = "20261118_1000_runner_hub_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNERS = "hosted_runners"
_FACTS = "hosted_runner_facts"
_FACTS_INDEX = "ix_hosted_runner_facts_runner_id_id"


def upgrade() -> None:
    held = set(sa.inspect(op.get_bind()).get_table_names())
    if _RUNNERS not in held:
        op.create_table(
            _RUNNERS,
            sa.Column("runner_id", sa.String(), primary_key=True),
            sa.Column("added_at", UtcDateTime(), nullable=False),
            sa.Column("added_by", sa.String(), nullable=False),
        )
    if _FACTS not in held:
        op.create_table(
            _FACTS,
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("runner_id", sa.String(), nullable=False),
            sa.Column("recorded_at", UtcDateTime(), nullable=False),
            sa.Column("op", sa.String(), nullable=False),
            sa.Column("actor", sa.String(), nullable=False),
            sa.Column("door", sa.String(), nullable=False),
        )
        op.create_index(_FACTS_INDEX, _FACTS, ["runner_id", "id"])


def downgrade() -> None:
    held = set(sa.inspect(op.get_bind()).get_table_names())
    if _FACTS in held:
        op.drop_index(_FACTS_INDEX, table_name=_FACTS)
        op.drop_table(_FACTS)
    if _RUNNERS in held:
        op.drop_table(_RUNNERS)
