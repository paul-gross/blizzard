"""graph policy facts — the per-graph follow-latest tri-state (hub store tree)

Issue #164 gives a graph a standing **follow-latest** policy: chunks pinned to this mint
re-pin to the newest enabled mint of the same name at their next transition.
``graph_policy_facts`` mirrors ``graph_lifecycle_facts`` exactly — append-only,
newest-fact-wins — with one difference: ``follow_latest`` is **nullable**, because the
value is a tri-state. NULL inherits ``HubConfig.follow_latest``; True/False override it.

Its own table rather than a column on ``graph_lifecycle_facts``: retire/re-enable and
follow-latest are independent lifecycles, and sharing a fact row would make every retire
also assert a policy. A graph with no row here reads NULL — inherit — so no backfill is
needed and every existing graph keeps today's pin-by-id behavior under the shipped
``follow_latest = false`` hub default.

Each revision in this tree creates a subset of the current ``schema`` metadata's tables
(the live-schema pattern); this one creates exactly the one new table, ``checkfirst`` so
a fresh ``base -> head`` and an in-place upgrade both converge.

Revision ID: 20260728_1200_hub_graph_policy_facts
Revises: 20260726_1200_hub_chunk_work_refs_rename
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import graph_policy_facts

revision: str = "20260728_1200_hub_graph_policy_facts"
down_revision: str | None = "20260726_1200_hub_chunk_work_refs_rename"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (graph_policy_facts,)


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
