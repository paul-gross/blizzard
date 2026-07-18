"""route capability token — append-only route_token_minted fact table (hub store tree, issue #84a)

Phase 5 of epic #85: an unguessable per-acquisition secret minted alongside a claim's
``route_created`` fact. This revision adds exactly one new table, ``route_token_minted``
(``id`` PK, ``chunk_id`` FK, ``token_hash``, ``seq``, ``minted_at``) — deliberately an
**append-only fact table**, not a ``token_hash`` column on ``route_created``
(``bzh:facts-not-status``): the route fact is immutable, so a later re-key (Phase 6)
appends a new token fact rather than rewriting the route row, the same reason
``route_released`` is its own table. ``seq`` shares the existing per-chunk
``route_created``/``route_released`` counter (``ChunkStore._next_route_seq``), so no
schema change is needed for that beyond the column itself.

Brand new as of this revision — no later revision reshapes it yet — so it is imported
directly from ``schema.py`` rather than frozen locally (the exception
``20260714_0819_hub_delivery_pr_facts`` documents), the same shape
``20260717_2345_hub_chunk_bounces`` used for ``chunk_bounces``.

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
