"""per-session spawn-preamble fingerprints — session_preamble_facts (issue #149)

The comparison key behind resume-time preamble elision: one row per spawn recording the
sha256 of the two *standing* preamble layers as that spawn resolved them — layer 1 (the
blizzard preamble) and layer 2 (the operator's workspace prompt). The next spawn that
resumes the session compares against the newest row and sends a layer only when it moved,
announcing the difference when it exists.

Digests, not the prose: the operator's text stays in ``workspace_prompt`` and in config,
and this table never becomes a second copy of it.

Append-only, keyed on the harness session — the thing that already holds the earlier
prose, and which outlives the per-attempt lease a node-entry resume mints fresh. No
back-fill is owed: a session with no row reads back ``None`` and renders in full, which is
exactly the pre-revision behaviour.

The table is declared as a frozen ``sa.Table`` literal in a local ``MetaData`` rather than
imported from ``schema.py`` (``bzh:frozen-revisions``), so a later reshape cannot silently
change what this revision creates on a fresh store.

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
