"""drop graph nodes run — the hub store loses `graph_nodes.run`, which nothing has read or written since 0.3.

Downgrade re-adds it as nullable Text and gives each hub row the ``run`` an older hub reads: its ``legacy_run`` where
it holds one, else the step list its action was in the packaged graphs, none for ``pass``. A row naming an action
with no step list — a test action — refuses the downgrade, naming the row.
Revision ID: 20261104_0900_drop_graph_nodes_run
Revises: 20261104_0900_binding_agents_retired
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0900_drop_graph_nodes_run"
down_revision: str | None = "20261104_0900_binding_agents_retired"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NODES = "graph_nodes"
_COLUMN = "run"

# The packaged step each action stood for, frozen as this revision ran.
_SCRIPTS = "python3 -m blizzard.hub.graphs.scripts."


def _step(name: str, command: str) -> str:
    return json.dumps([{"command": command, "name": name, "produces": None}])


def _garden_command(args: dict[str, list[str]]) -> str:
    flags = [f"--{key} {shlex.quote(value)}" for key in ("delta", "proposals") for value in args.get(key, [])]
    return " ".join([f"{_SCRIPTS}garden_deliver", *flags])


def _inverse_run(node_id: str, action: str, raw_args: str | None) -> str | None:
    """The ``run`` JSON a row naming ``action`` is given on the way down; ``None`` for ``pass``, whose step list was
    empty. An action with no step list — a test action — raises, naming the row."""
    args: dict[str, list[str]] = json.loads(raw_args) if raw_args else {}
    if action == "land":
        return _step("land-every-repo", f"{_SCRIPTS}land_pr_ci")
    if action == "record-findings":
        return _step("materialize-review-findings", f"{_SCRIPTS}review_deliver")
    if action == "garden-deliver":
        return _step("materialize-garden-artifacts", _garden_command(args))
    if action == "pass":
        return None
    raise RuntimeError(
        f"graph node {node_id!r} names action {action!r}, which has no step list to restore — "
        f"retire its graph before downgrading past {revision}"
    )


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_NODES)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy a stopped batch left (SQLite makes it outside the batch's transaction, under Alembic's name)."""
    copy = f"_alembic_tmp_{_NODES}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps the unnamed ``CHECK`` its own reflection drops."""
    return sa.Table(_NODES, sa.MetaData(), autoload_with=bind)


def _runs(bind: sa.Connection, held: set[str]) -> dict[str, str]:
    """Each hub row's restored ``run`` by node id, computed before any write so a refused row changes nothing."""
    unfilled = " AND run IS NULL" if _COLUMN in held else ""
    rows = bind.execute(
        sa.text(
            "SELECT node_id, legacy_run, action, action_args FROM graph_nodes"
            f" WHERE executor = 'hub'{unfilled} ORDER BY node_id"
        )
    ).all()
    runs: dict[str, str] = {}
    for node_id, legacy_run, action, raw_args in rows:
        run: str | None = legacy_run
        if run is None and action is not None:
            run = _inverse_run(node_id, action, raw_args)
        if run is not None:
            runs[node_id] = run
    return runs


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN in _columns(bind):
        _clear_stale_copy(bind)
        with op.batch_alter_table(_NODES, recreate="auto", copy_from=_reflected(bind)) as batch:
            batch.drop_column(_COLUMN)


def downgrade() -> None:
    bind = op.get_bind()
    held = _columns(bind)
    runs = _runs(bind, held)
    if _COLUMN not in held:
        _clear_stale_copy(bind)
        with op.batch_alter_table(_NODES, recreate="auto", copy_from=_reflected(bind)) as batch:
            batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
    for node_id, run in runs.items():
        bind.execute(sa.text("UPDATE graph_nodes SET run = :run WHERE node_id = :id"), {"run": run, "id": node_id})
