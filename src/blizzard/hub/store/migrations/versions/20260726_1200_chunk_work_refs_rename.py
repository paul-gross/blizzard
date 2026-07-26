"""``chunk_pm_pointers`` -> ``chunk_work_refs`` (issue #55, hub store tree)

The PM vocabulary is renamed to the work-source terminology the domain already uses:
a ``{source, ref}`` pair naming an item in a configured ``[[work_source]]`` is a **work
ref**, not a "PM pointer" — a word that collided with the unrelated *pointer artifact*
(``bzh:never-code``). This revision carries the store side of that rename: the table,
and nothing else.

**Pure rename — no reshape, no backfill.** Columns, types, nullability, the primary key,
and the ``chunk_id`` foreign key are all untouched, so every existing row (including the
``source="blizzard"`` values ``20260716_1512_hub_pm_pointer_source_ref`` backfilled)
survives byte-for-byte under the new name. ``op.rename_table`` is portable across both
supported backends (``bzh:sql-portable``); SQLite implements it natively, so the
``batch_alter_table`` dance a column reshape needs does not apply here.

**The earlier revisions keep the old name** (``canon:no-retro``): ``0011`` still creates
``chunk_pm_pointers`` and ``0013`` still reshapes it under that name. A migration means
what it meant when it ran, and the chain reaches this rename by replaying them in order
— rewriting them would change what a historical revision does on a future checkout, the
same reasoning ``0013``'s own docstring records for its local table literals.

Guarded like ``20260725_1200_graph_checks_gating``: the rename fires only when the old
table is the one actually present, so a fresh ``base -> head``, an in-place upgrade of a
live store, and a down-then-up cycle all land at exactly one ``chunk_work_refs``. The
guard is about the table names on disk, not the revision bookkeeping — alembic already
declines to re-run an applied revision, so what this protects against is a store whose
schema and revision row disagree (a hand-renamed table, a partially applied step).

Revision ID: 20260726_1200_hub_chunk_work_refs_rename
Revises: 20260725_1200_hub_graph_checks_gating
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260726_1200_hub_chunk_work_refs_rename"
down_revision: str | None = "20260725_1200_hub_graph_checks_gating"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_NAME = "chunk_pm_pointers"
_NEW_NAME = "chunk_work_refs"


def _tables(bind: sa.Connection) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def upgrade() -> None:
    tables = _tables(op.get_bind())
    if _OLD_NAME in tables and _NEW_NAME not in tables:
        op.rename_table(_OLD_NAME, _NEW_NAME)


def downgrade() -> None:
    tables = _tables(op.get_bind())
    if _NEW_NAME in tables and _OLD_NAME not in tables:
        op.rename_table(_NEW_NAME, _OLD_NAME)
