"""hub event log move — the hub's own export occurrences leave every tenant's ``event_log`` for ``hub_event_log``.

Every ``event_log`` row of one of the eight export kinds that names no runner, chunk, lease or node — the occurrences
of the hub's own trace and egress exports, which concern no tenant — moves to ``hub_event_log`` with its ``id``,
``recorded_at``, ``severity``, ``kind``, ``message`` and ``detail``. A runner's report of its own trace export names
its runner and stays in its tenant's log. The move pages along ``event_log.id`` in bounded batches, then deletes each
page from ``event_log``. A row whose id ``hub_event_log`` holds with the same ``kind``, ``recorded_at`` and ``message``
was copied by a run stopped part way and is not copied again; one whose id ``hub_event_log`` gives another event — a
hub occurrence written after an earlier upgrade the downgrade left behind — is copied under a fresh id, and the count
logged; every other row keeps its id. ``hub_event_log`` then gains ``ix_hub_event_log_kind_recorded_at`` for the
exports' newest-of-a-kind reads.

The downgrade refuses while more than one tenant exists, naming them; with one it moves every row of the eight kinds
back into ``event_log``, stamped with that tenant and naming no runner, chunk, lease or node, under its own id when
``event_log`` holds none by it and a fresh one otherwise; rows of the hub's other kinds stay. It drops the index. Each
direction leaves the Postgres id sequence of the log it copied into past every id copied in under its own.
Revision ID: 20261008_1600_hub_event_log_move
Revises: 20261008_1500_scope_surrogate
"""

from __future__ import annotations

import logging
from collections.abc import Iterator, Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261008_1600_hub_event_log_move"
down_revision: str | None = "20261008_1500_scope_surrogate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_ROWS = 1000
_KIND_INDEX = "ix_hub_event_log_kind_recorded_at"

# Restated, not imported, from ``blizzard.foundation.event_log`` (``bzh:frozen-revisions``): the export kinds.
_EXPORT_KINDS = (
    "trace-export-failed",
    "trace-export-recovered",
    "trace-window-skipped",
    "trace-config-rejected",
    "egress-write-failed",
    "egress-write-recovered",
    "egress-config-rejected",
    "egress-cursor-reset",
)

# The columns both logs carry, in the order each copy names them.
_CARRIED = ("id", "recorded_at", "severity", "kind", "message", "detail")
_UNNAMED = ("runner_id", "chunk_id", "lease_id", "node_name")

_event_log = sa.table(
    "event_log",
    sa.column("id", sa.Integer),
    sa.column("recorded_at", UtcDateTime),
    sa.column("severity", sa.String),
    sa.column("kind", sa.String),
    sa.column("runner_id", sa.String),
    sa.column("chunk_id", sa.String),
    sa.column("lease_id", sa.String),
    sa.column("node_name", sa.String),
    sa.column("message", sa.Text),
    sa.column("detail", sa.Text),
    sa.column("tenant_id", sa.String),
)
_hub_event_log = sa.table(
    "hub_event_log",
    sa.column("id", sa.Integer),
    sa.column("recorded_at", UtcDateTime),
    sa.column("severity", sa.String),
    sa.column("kind", sa.String),
    sa.column("message", sa.Text),
    sa.column("detail", sa.Text),
)
_tenants = sa.table("tenants", sa.column("tenant_id", sa.String))

_log = logging.getLogger("alembic.runtime.migration")


def _tenant_ids(bind: sa.Connection) -> list[str]:
    return list(bind.execute(sa.select(_tenants.c.tenant_id).order_by(_tenants.c.tenant_id)).scalars())


def _index_names(bind: sa.Connection, table: str) -> set[str]:
    return {str(index["name"]) for index in sa.inspect(bind).get_indexes(table)}


def _pages(bind: sa.Connection, table: sa.TableClause, *where: sa.ColumnElement[bool]) -> Iterator[list[int]]:
    """The ids of ``table``'s rows matching ``where``, along the id, ``_BATCH_ROWS`` to a page — each page read once
    the caller is done with the one before it, by a seek past that page's last id, never a rescan from the start."""
    ids = table.c.id
    cursor = 0
    while page := list(
        bind.execute(sa.select(ids).where(*where, ids > cursor).order_by(ids).limit(_BATCH_ROWS)).scalars()
    ):
        yield page
        cursor = page[-1]


def _held(bind: sa.Connection, table: sa.TableClause, page: list[int]) -> list[int]:
    """The ids of ``page`` ``table`` already holds — read before any copy into it, so the copy cannot see itself."""
    return list(bind.execute(sa.select(table.c.id).where(table.c.id.in_(page))).scalars())


def _sync_id_sequence(bind: sa.Connection, table: sa.TableClause) -> None:
    """Postgres keeps an ``id`` sequence that rows inserted under an explicit id leave behind; move it past them.
    SQLite takes the next id from the table's own rows, so it has nothing to move."""
    if bind.dialect.name != "postgresql":
        return
    newest = sa.select(sa.func.coalesce(sa.func.max(table.c.id), 0) + 1).scalar_subquery()
    bind.execute(sa.select(sa.func.setval(sa.func.pg_get_serial_sequence(table.name, "id"), newest, False)))


def _already_copied(bind: sa.Connection, page: list[int]) -> set[int]:
    """The ids of ``page`` whose ``hub_event_log`` row is the same event — one a run stopped part way copied — rather
    than another event under the same id."""
    e, h = _event_log.c, _hub_event_log.c
    same = sa.and_(h.id == e.id, h.kind == e.kind, h.recorded_at == e.recorded_at, h.message == e.message)
    return set(
        bind.execute(sa.select(e.id).select_from(_event_log.join(_hub_event_log, same)).where(e.id.in_(page))).scalars()
    )


def _move_up(bind: sa.Connection) -> int:
    e = _event_log.c
    moving = (e.kind.in_(_EXPORT_KINDS), *(e[name].is_(None) for name in _UNNAMED))
    moved = reissued = 0
    for page in _pages(bind, _event_log, *moving):
        taken = _held(bind, _hub_event_log, page)
        colliding = sorted(set(taken) - _already_copied(bind, page))
        kept = sa.select(*(e[name] for name in _CARRIED)).where(e.id.in_(page), e.id.not_in(taken))
        bind.execute(sa.insert(_hub_event_log).from_select(list(_CARRIED), kept))
        if colliding:
            _sync_id_sequence(bind, _hub_event_log)
            fresh = [name for name in _CARRIED if name != "id"]
            bind.execute(
                sa.insert(_hub_event_log).from_select(
                    fresh, sa.select(*(e[name] for name in fresh)).where(e.id.in_(colliding)).order_by(e.id)
                )
            )
            reissued += len(colliding)
        bind.execute(sa.delete(_event_log).where(e.id.in_(page)))
        moved += len(page)
    if reissued:
        _log.info("%d hub export events took fresh hub_event_log ids; their own were taken", reissued)
    _sync_id_sequence(bind, _hub_event_log)
    return moved


def upgrade() -> None:
    bind = op.get_bind()
    moved = _move_up(bind)
    if moved:
        _log.info("moved %d hub export events from event_log to hub_event_log", moved)
    if _KIND_INDEX not in _index_names(bind, "hub_event_log"):
        op.create_index(_KIND_INDEX, "hub_event_log", ["kind", "recorded_at"])


def _move_down(bind: sa.Connection, tenant_id: str | None) -> None:
    h = _hub_event_log.c
    for page in _pages(bind, _hub_event_log, h.kind.in_(_EXPORT_KINDS)):
        if tenant_id is None:
            raise RuntimeError("hub_event_log holds export events but the store holds no tenant to move them into")
        taken = _held(bind, _event_log, page)
        stamped = (sa.literal(tenant_id, sa.String).label("tenant_id"),)
        kept = (*(h[name] for name in _CARRIED), *stamped)
        fresh = (*(h[name] for name in _CARRIED if name != "id"), *stamped)
        bind.execute(
            sa.insert(_event_log).from_select(
                [*_CARRIED, "tenant_id"], sa.select(*kept).where(h.id.in_(page), h.id.not_in(taken))
            )
        )
        if taken:
            _sync_id_sequence(bind, _event_log)
        reissued = bind.execute(
            sa.insert(_event_log).from_select(
                [name for name in _CARRIED if name != "id"] + ["tenant_id"],
                sa.select(*fresh).where(h.id.in_(page), h.id.in_(taken)).order_by(h.id),
            )
        ).rowcount
        if reissued:
            _log.info("%d hub export events took fresh event_log ids; their own were taken", reissued)
        bind.execute(sa.delete(_hub_event_log).where(h.id.in_(page)))
    _sync_id_sequence(bind, _event_log)


def downgrade() -> None:
    bind = op.get_bind()
    tenants = _tenant_ids(bind)
    if len(tenants) > 1:
        raise RuntimeError(f"delete every tenant but one before downgrading; the store holds {', '.join(tenants)}")
    if _KIND_INDEX in _index_names(bind, "hub_event_log"):
        op.drop_index(_KIND_INDEX, table_name="hub_event_log")
    _move_down(bind, tenants[0] if tenants else None)
