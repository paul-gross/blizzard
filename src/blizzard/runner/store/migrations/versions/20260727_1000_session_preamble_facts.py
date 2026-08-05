"""per-session spawn-preamble fingerprints (issue #149): one row per spawn holding the sha256 of the
two standing preamble layers, so a resume sends a layer only when it moved. Digests, not prose.

Revision ID: 20260727_1000_runner_session_preamble_facts
Revises: 20260726_1000_runner_declaration_environment_id
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20260727_1000_runner_session_preamble_facts"
down_revision: str | None = "20260726_1000_runner_declaration_environment_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_frozen_metadata = sa.MetaData()

session_preamble_facts = sa.Table(
    "session_preamble_facts",
    _frozen_metadata,
    sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
    sa.Column("session_id", sa.String, nullable=False),
    sa.Column("blizzard_digest", sa.String, nullable=False),
    sa.Column("workspace_digest", sa.String, nullable=False),
    sa.Column("recorded_at", UtcDateTime, nullable=False),
)

_TABLES = (session_preamble_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
