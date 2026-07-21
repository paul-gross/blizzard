"""SSO federation jti replay cache — jwt_jti_seen (runner store tree, issue #95, D4)

The single-use guard the runner's federation callback (`runner/auth/federation.py`)
checks a hub-signed JWT's `jti` claim against before ever minting its own session —
store-backed (decision D4) so the single-use guarantee survives a runner restart within
the JWT's own short lifetime. The `jti` primary key alone is the whole guarantee; see
`runner/auth/jti_cache.py`'s module docstring for the crash-correctness position (no
`bzh:crash-point-registry` entry, no new `bzh:invariant-checker` assertion required).

Each revision in this tree creates a subset of the current `schema` metadata's tables
(the live-schema pattern, mirroring `20260719_1100_runner_nudge_facts`); this one
creates exactly the one new table, `checkfirst` so a fresh `base -> head` and an
in-place upgrade both converge.

Revision ID: 20260721_1500_runner_jwt_jti_seen
Revises: 20260719_1100_runner_nudge_facts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import jwt_jti_seen

revision: str = "20260721_1500_runner_jwt_jti_seen"
down_revision: str | None = "20260719_1100_runner_nudge_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (jwt_jti_seen,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
