"""hub actions — a hub node may name an action in place of ``run``, and a waiting pass may name its next poll.

Adds nullable `graph_nodes.action`, `graph_nodes.action_args`, and `hub_node_poll.next_poll_at`, writing no row.
Downgrade first gives every hub node naming an action and no `run` the step list that action was in the packaged
graphs, so a version minted onto an action still runs under the older hub, then drops the three columns.
Revision ID: 20261023_0901_hub_actions
Revises: 20261023_0900_route_released_cause
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261023_0901_hub_actions"
down_revision: str | None = "20261023_0900_route_released_cause"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NODES = "graph_nodes"
_POLL = "hub_node_poll"
_COLUMNS: dict[str, tuple[tuple[str, sa.types.TypeEngine[Any]], ...]] = {
    _NODES: (("action", sa.String()), ("action_args", sa.Text())),
    _POLL: (("next_poll_at", UtcDateTime()),),
}

_SCRIPTS = "python3 -m blizzard.hub.graphs.scripts."


def _step(name: str, command: str) -> list[dict[str, str | None]]:
    return [{"command": command, "name": name, "produces": None}]


def _garden_command(args: dict[str, list[str]]) -> str:
    flags = [f"--{key} {shlex.quote(value)}" for key in ("delta", "proposals") for value in args.get(key, [])]
    return " ".join([f"{_SCRIPTS}garden_deliver", *flags])


def _inverse_run(node_id: str, action: str, raw_args: str | None) -> str | None:
    """The ``run`` JSON a row naming ``action`` is given on the way down; ``None`` for ``pass``,
    whose step list was empty. An action with no inverse — a test action — raises, naming the row."""
    args: dict[str, list[str]] = json.loads(raw_args) if raw_args else {}
    if action == "land":
        return json.dumps(_step("land-every-repo", f"{_SCRIPTS}land_pr_ci"))
    if action == "record-findings":
        return json.dumps(_step("materialize-review-findings", f"{_SCRIPTS}review_deliver"))
    if action == "garden-deliver":
        return json.dumps(_step("materialize-garden-artifacts", _garden_command(args)))
    if action == "pass":
        return None
    raise RuntimeError(
        f"graph node {node_id!r} names action {action!r}, which has no step list to restore — "
        f"retire its graph before downgrading past {revision}"
    )


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(table)}


def _clear_stale_copy(bind: sa.Connection, table: str) -> None:
    """Drop the copy an interrupted batch left (SQLite makes it outside the batch's transaction)."""
    copy = f"_alembic_tmp_{table}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection, table: str) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps its unnamed ``CHECK`` constraints."""
    return sa.Table(table, sa.MetaData(), autoload_with=bind)


def upgrade() -> None:
    bind = op.get_bind()
    for table, columns in _COLUMNS.items():
        held = _columns(bind, table)
        missing = [(name, kind) for name, kind in columns if name not in held]
        if not missing:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for name, kind in missing:
                batch.add_column(sa.Column(name, kind, nullable=True))


def _restore_runs(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT node_id, action, action_args FROM graph_nodes"
            " WHERE executor = 'hub' AND run IS NULL AND action IS NOT NULL"
        )
    ).all()
    for node_id, action, raw_args in rows:
        run = _inverse_run(node_id, action, raw_args)
        if run is not None:
            bind.execute(sa.text("UPDATE graph_nodes SET run = :run WHERE node_id = :id"), {"run": run, "id": node_id})


def downgrade() -> None:
    bind = op.get_bind()
    if "action" in _columns(bind, _NODES):
        _restore_runs(bind)
    for table, columns in _COLUMNS.items():
        held = _columns(bind, table)
        present = [name for name, _ in columns if name in held]
        if not present:
            continue
        _clear_stale_copy(bind, table)
        with op.batch_alter_table(table, recreate="auto", copy_from=_reflected(bind, table)) as batch:
            for name in present:
                batch.drop_column(name)
