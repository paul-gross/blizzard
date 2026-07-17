"""fleet registry — runner registrations and pause/resume facts (hub store tree)

The P7W3 runner-registry tables (design/domain/fleet.md, D-019/D-070/D-043):

* ``runner_registrations`` — one upserted row per runner (runner_id + workspace_id)
  with a refreshed ``last_seen_at`` liveness derives from (D-070).
* ``runner_pause_facts`` — append-only pause/resume facts; ``paused`` derives from
  the newest one, read back by the runner on its outbound pull and adhered to (D-043).

Parents before children so the FK from ``runner_pause_facts`` resolves.

Revision ID: 20260713_1947_hub_runner_registry
Revises: 20260713_1946_hub_queue_shaping
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

from blizzard.hub.store.schema import runner_pause_facts, runner_registrations

revision: str = "20260713_1947_hub_runner_registry"
down_revision: str | None = "20260713_1946_hub_queue_shaping"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = [runner_registrations, runner_pause_facts]


def upgrade() -> None:
    bind = op.get_bind()
    for table in _TABLES:
        table.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(_TABLES):
        table.drop(bind, checkfirst=True)
