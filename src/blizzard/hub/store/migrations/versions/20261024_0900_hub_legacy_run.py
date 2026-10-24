"""hub legacy run — every stored hub node naming no action is carried onto the action its ``run`` already named.

Adds nullable `graph_nodes.legacy_run`. Each hub row with no `action` whose `run` ran one packaged script, or no step
at all, is given that script's action and `with:` arguments (`pass` for no step); every other such row keeps its `run`
copied into `legacy_run`, logged one line per row. `run` itself is never written. Downgrade clears the action of every
row still holding a `run`, then drops `legacy_run`; a row translated from no `run` keeps `pass`, which the parent
revision's downgrade restores.
Revision ID: 20261024_0900_hub_legacy_run
Revises: 20261023_0901_hub_actions
"""

from __future__ import annotations

import json
import logging
import shlex
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261024_0900_hub_legacy_run"
down_revision: str | None = "20261023_0901_hub_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_log = logging.getLogger("alembic.runtime.migration")

_NODES = "graph_nodes"
_COLUMN = "legacy_run"

# Each packaged script a stored step ran, by module path, and the action it was — frozen as this revision ran.
_ACTIONS = {
    "blizzard.hub.graphs.scripts.land_pr_ci": "land",
    "blizzard.hub.graphs.scripts.land_default": "land",
    "blizzard.hub.graphs.scripts.land_ff": "land",
    "blizzard.hub.graphs.scripts.review_deliver": "record-findings",
    "blizzard.hub.graphs.scripts.garden_deliver": "garden-deliver",
}
_FLAGS = {"garden-deliver": {"--delta": "delta", "--proposals": "proposals"}}
_INTERPRETERS = frozenset({"python3", "python"})


def _flag_args(argv: list[str], flags: dict[str, str]) -> dict[str, list[str]] | None:
    """``argv`` read as ``flag value`` (or ``flag=value``) pairs over ``flags``; ``None`` when it carries anything
    else."""
    args: dict[str, list[str]] = {}
    rest = list(argv)
    while rest:
        flag, _, value = rest.pop(0).partition("=")
        if flag not in flags:
            return None
        if not value:
            if not rest:
                return None
            value = rest.pop(0)
        args.setdefault(flags[flag], []).append(value)
    return args


def _translated(raw_run: str | None) -> tuple[str, dict[str, list[str]]] | None:
    """The action and arguments a stored ``run`` stood for, or ``None`` when it stood for none."""
    steps = json.loads(raw_run) if raw_run else []
    if not steps:
        return "pass", {}
    if len(steps) != 1 or not isinstance(steps[0], dict):
        return None
    try:
        argv = shlex.split(str(steps[0].get("command", "")))
    except ValueError:
        return None
    if len(argv) < 3 or argv[0] not in _INTERPRETERS or argv[1] != "-m" or argv[2] not in _ACTIONS:
        return None
    action = _ACTIONS[argv[2]]
    args = _flag_args(argv[3:], _FLAGS.get(action, {}))
    return None if args is None else (action, args)


def _columns(bind: sa.Connection) -> set[str]:
    return {str(c["name"]) for c in sa.inspect(bind).get_columns(_NODES)}


def _clear_stale_copy(bind: sa.Connection) -> None:
    """Drop the copy an interrupted batch left (SQLite makes it outside the batch's transaction)."""
    copy = f"_alembic_tmp_{_NODES}"[:50]
    if sa.inspect(bind).has_table(copy):
        op.drop_table(copy)


def _reflected(bind: sa.Connection) -> sa.Table:
    """The table as the store holds it, so a copying batch keeps its unnamed ``CHECK`` constraints."""
    return sa.Table(_NODES, sa.MetaData(), autoload_with=bind)


def _carry(bind: sa.Connection) -> None:
    rows = bind.execute(
        sa.text(
            "SELECT n.node_id, n.name, n.run, n.tenant_id, g.graph_id, g.name, g.created_at"
            " FROM graph_nodes n JOIN graphs g ON g.graph_id = n.graph_id"
            " WHERE n.executor = 'hub' AND n.action IS NULL"
            " ORDER BY g.created_at, g.graph_id, n.node_id"
        )
    ).all()
    for node_id, node_name, raw_run, tenant, graph_id, graph_name, created_at in rows:
        carried = _translated(raw_run)
        if carried is None:
            bind.execute(
                sa.text("UPDATE graph_nodes SET legacy_run = run WHERE node_id = :id"),
                {"id": node_id},
            )
            _log.warning(
                "graph %s (%r, minted %s) hub node %r of tenant %s names no action its run translates to; "
                "its run is kept as legacy_run, and a chunk standing at it escalates",
                graph_id,
                graph_name,
                created_at,
                node_name,
                tenant,
            )
            continue
        action, args = carried
        bind.execute(
            sa.text("UPDATE graph_nodes SET action = :action, action_args = :args WHERE node_id = :id"),
            {"action": action, "args": json.dumps(args) if args else None, "id": node_id},
        )


def upgrade() -> None:
    bind = op.get_bind()
    if _COLUMN not in _columns(bind):
        _clear_stale_copy(bind)
        with op.batch_alter_table(_NODES, recreate="auto", copy_from=_reflected(bind)) as batch:
            batch.add_column(sa.Column(_COLUMN, sa.Text(), nullable=True))
    _carry(bind)


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("UPDATE graph_nodes SET action = NULL, action_args = NULL WHERE run IS NOT NULL"))
    if _COLUMN in _columns(bind):
        _clear_stale_copy(bind)
        with op.batch_alter_table(_NODES, recreate="auto", copy_from=_reflected(bind)) as batch:
            batch.drop_column(_COLUMN)
