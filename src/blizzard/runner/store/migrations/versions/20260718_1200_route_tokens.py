"""route capability token stash — route_tokens (runner store tree, issue #84a)

Phase 5 of epic #85: the runner's local stash of a won claim's plaintext route
token (`wire.route.RouteClaimResponse.route_token`). One upserted row per chunk
(`chunk_id` PK, `token`, `acquired_at`), mirroring `hub_control`'s shape — a fresh
claim overwrites the prior token, a same-runner requeue/takeover/retry re-reads the
same row. Unlike the hub's own append-only `route_token_minted` facts, the runner
keeps no rotation history: it only ever presents its *current* token.

Each revision in this tree creates a subset of the current ``schema`` metadata's
tables (the live-schema pattern); this one creates exactly the one new table,
``checkfirst`` so a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260718_1200_runner_route_tokens
Revises: 20260717_2200_runner_usage_facts
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.runner.store.schema import route_tokens

revision: str = "20260718_1200_runner_route_tokens"
down_revision: str | None = "20260717_2200_runner_usage_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (route_tokens,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
