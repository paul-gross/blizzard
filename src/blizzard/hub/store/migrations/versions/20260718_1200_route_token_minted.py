"""route capability token — creates the append-only ``route_token_minted`` fact table:
one unguessable per-acquisition secret per claim (hub store tree, issue #84a)

Revision ID: 20260718_1200_hub_route_token_minted
Revises: 20260718_1130_hub_runner_token
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import route_token_minted

revision: str = "20260718_1200_hub_route_token_minted"
down_revision: str | None = "20260718_1130_hub_runner_token"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    route_token_minted.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    route_token_minted.drop(bind, checkfirst=True)
