"""Key nothing in the runner store on the runner's id: drop ``runner_id`` from every table holding it,
and add the single-row ``runner_identity``, left empty for the runner's next successful registration.
``downgrade()`` is lossy: it re-keys every row by the identity row's name.
Revision ID: 20261006_1000_runner_identity
Revises: 20261005_1000_runner_credential_renewal_facts
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from blizzard.foundation.store.utc import UtcDateTime

revision: str = "20261006_1000_runner_identity"
down_revision: str | None = "20261005_1000_runner_credential_renewal_facts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RUNNER_ID = "runner_id"
_HISTORY_TABLES = ("local_pause_facts", "leases")
_PRE_CHANGE_DEFAULT_ID = "runner-local"

# Frozen literals (`bzh:frozen-revisions`): each table's shape either side of this revision.
_identity = sa.Table(
    "runner_identity",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String(), nullable=False),
    sa.Column("runner_name", sa.String(), nullable=False),
    sa.Column("registered_at", UtcDateTime(), nullable=False),
)

_hub_control = sa.Table(
    "hub_control",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("paused", sa.Boolean(), nullable=False),
    sa.Column("updated_at", UtcDateTime(), nullable=False),
)

_keyed_hub_control = sa.Table(
    "hub_control",
    sa.MetaData(),
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("paused", sa.Boolean(), nullable=False),
    sa.Column("updated_at", UtcDateTime(), nullable=False),
)

_daemon_liveness = sa.Table(
    "daemon_liveness",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("alive_at", UtcDateTime(), nullable=False),
)

_keyed_daemon_liveness = sa.Table(
    "daemon_liveness",
    sa.MetaData(),
    sa.Column("runner_id", sa.String(), primary_key=True),
    sa.Column("alive_at", UtcDateTime(), nullable=False),
)

_keyed_local_pause_facts = sa.Table(
    "local_pause_facts",
    sa.MetaData(),
    sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
    sa.Column("runner_id", sa.String(), nullable=False),
    sa.Column("paused", sa.Boolean(), nullable=False),
    sa.Column("set_at", UtcDateTime(), nullable=False),
    sa.Column("set_by", sa.String(), nullable=False),
    sa.Column("reason", sa.String(), nullable=True),
)
#: Who the clear the upgrade appends for a kept id with no local fact of its own says set it.
_MIGRATION_SETTER = "migration"

# Each singleton, runner-keyed then rebuilt, with the instant its newest row is ordered by.
_SINGLETONS = (
    (_keyed_daemon_liveness, _daemon_liveness, "alive_at"),
    (_keyed_hub_control, _hub_control, "updated_at"),
)


def _columns(bind: sa.Connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _kept_runner_id(bind: sa.Connection) -> str | None:
    """The id whose singleton rows survive: the newest liveness beat's, else the newest mirror's."""
    for keyed, _singleton, at in _SINGLETONS:
        if _RUNNER_ID in _columns(bind, keyed.name):
            newest = sa.select(keyed.c.runner_id).order_by(keyed.c[at].desc(), keyed.c.runner_id.desc()).limit(1)
            row = bind.execute(newest).first()
            if row is not None:
                return str(row.runner_id)
    return None


def _collapse(bind: sa.Connection, keyed: sa.Table, singleton: sa.Table, kept: str | None) -> None:
    """Rebuild ``keyed`` as ``singleton``, carrying only the kept id's row."""
    if _RUNNER_ID not in _columns(bind, keyed.name):
        return
    carried = [c.name for c in singleton.columns if c.name != "id"]
    row = None
    if kept is not None:
        row = bind.execute(sa.select(*(keyed.c[n] for n in carried)).where(keyed.c.runner_id == kept)).first()
    keyed.drop(bind)
    singleton.create(bind)
    if row is not None:
        bind.execute(singleton.insert().values(**row._mapping))


def _keep_local_brake(bind: sa.Connection, kept: str | None) -> None:
    """Re-append the kept id's newest local fact (else a clear) when another id wrote the newest one,
    since the brake reads the newest fact whichever id wrote it."""
    facts = _keyed_local_pause_facts
    if kept is None or _RUNNER_ID not in _columns(bind, facts.name):
        return
    newest = bind.execute(sa.select(facts).order_by(facts.c.id.desc()).limit(1)).first()
    if newest is None or newest.runner_id == kept:
        return
    own = bind.execute(sa.select(facts).where(facts.c.runner_id == kept).order_by(facts.c.id.desc()).limit(1)).first()
    carried = (
        {"paused": own.paused, "set_at": own.set_at, "set_by": own.set_by, "reason": own.reason}
        if own is not None
        else {"paused": False, "set_at": newest.set_at, "set_by": _MIGRATION_SETTER, "reason": None}
    )
    bind.execute(facts.insert().values(runner_id=kept, **carried))


def _expand(bind: sa.Connection, singleton: sa.Table, keyed: sa.Table, key: str) -> None:
    """Rebuild ``singleton`` as ``keyed``, its row (if any) keyed by ``key``."""
    if _RUNNER_ID in _columns(bind, singleton.name):
        return
    carried = [c.name for c in singleton.columns if c.name != "id"]
    newest = sa.select(*(singleton.c[n] for n in carried)).order_by(singleton.c.id.desc()).limit(1)
    row = bind.execute(newest).first()
    singleton.drop(bind)
    keyed.create(bind)
    if row is not None:
        bind.execute(keyed.insert().values(runner_id=key, **row._mapping))


def _downgrade_key(bind: sa.Connection) -> str:
    if _identity.name not in set(sa.inspect(bind).get_table_names()):
        return _PRE_CHANGE_DEFAULT_ID
    row = bind.execute(sa.select(_identity.c.runner_name).order_by(_identity.c.id.desc()).limit(1)).first()
    return str(row.runner_name) if row is not None else _PRE_CHANGE_DEFAULT_ID


def upgrade() -> None:
    bind = op.get_bind()
    kept = _kept_runner_id(bind)
    for keyed, singleton, _at in _SINGLETONS:
        _collapse(bind, keyed, singleton, kept)
    _keep_local_brake(bind, kept)
    for table in _HISTORY_TABLES:
        if _RUNNER_ID in _columns(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.drop_column(_RUNNER_ID)
    _identity.create(bind, checkfirst=True)


def downgrade() -> None:
    bind = op.get_bind()
    key = _downgrade_key(bind)
    for keyed, singleton, _at in _SINGLETONS:
        _expand(bind, singleton, keyed, key)
    for table in _HISTORY_TABLES:
        if _RUNNER_ID not in _columns(bind, table):
            with op.batch_alter_table(table) as batch:
                batch.add_column(sa.Column(_RUNNER_ID, sa.String(), nullable=True))
            bind.execute(sa.table(table, sa.column(_RUNNER_ID)).update().values({_RUNNER_ID: key}))
            with op.batch_alter_table(table) as batch:
                batch.alter_column(_RUNNER_ID, existing_type=sa.String(), nullable=False)
    _identity.drop(bind, checkfirst=True)
