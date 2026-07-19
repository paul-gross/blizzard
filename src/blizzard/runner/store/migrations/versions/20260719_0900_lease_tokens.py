"""lease capability token stash — lease_tokens (runner store tree, issue #113 Phase 1)

The runner's local stash of a lease's minted capability token hash: one row per
lease (``lease_id`` PK, ``token_hash``, ``minted_at``), written once at spawn
alongside the lease mint itself. The plaintext rides the spawn env
(``BLIZZARD_LEASE_TOKEN``) and is never persisted — only its sha256 hash lands
here, mirroring how the hub keeps only ``route_token_minted`` hashes. This
revision is pure additive scaffold: no caller yet reads the hash back to
authorize anything.

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the one new table,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260719_0900_runner_lease_tokens
Revises: 20260718_1200_runner_route_tokens
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import lease_tokens

revision: str = "20260719_0900_runner_lease_tokens"
down_revision: str | None = "20260718_1200_runner_route_tokens"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (lease_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
