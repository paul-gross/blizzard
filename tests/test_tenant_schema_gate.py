"""The tenant schema gate — the build check over the hub's ``schema.metadata`` (``bzh:tenant-keyed-table``,
``bzh:global-tables-closed``).

A table is tenant-owned exactly when it is not on ``GLOBAL_TABLES``. A tenant-owned table carries ``tenant_id NOT
NULL`` with a foreign key to ``tenants``; every primary key, unique constraint and unique index it declares holds
``tenant_id`` or a hub-unique column; it has an index led by ``tenant_id``, a tenant-led primary key or unique
constraint counting as one; and every constraint and index name it declares fits Postgres's 63-character identifier
limit. Each rule is a function over a ``MetaData`` returning its faults, so the cases below can prove each one fails a
deliberately broken schema rather than passing vacuously."""

from __future__ import annotations

from collections.abc import Callable

import pytest
import sqlalchemy as sa
from sqlalchemy.sql.schema import SchemaItem

from blizzard.hub.store import schema

pytestmark = pytest.mark.unit

_TENANT_ID = "tenant_id"
_IDENTIFIER_LIMIT = 63

# The global tables that carry a ``tenant_id`` column without being tenant-owned, each with why.
_GLOBAL_TABLES_CARRYING_TENANT_ID: dict[str, str] = {
    "tenants": "the boundary itself: its tenant_id is the key every tenant foreign key names",
    "membership_facts": "carries the tenant it grants into as data, read hub-wide to list a person's tenants",
    "tenant_deletions": "names the tenant whose teardown it audits, and outlives it",
}

# Names longer than the limit that predate it and that no revision renames, each with why it stands.
_LONG_NAMES_KEPT: dict[str, str] = {}

# Keys the build check still fails, each naming the revision that rescopes it; that revision deletes its entry.
_PENDING_KEYS: dict[str, str] = {}

Rule = Callable[[sa.MetaData, frozenset[str]], list[str]]


def _tenant_owned(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[sa.Table]:
    return [table for name, table in sorted(metadata.tables.items()) if name not in global_tables]


def unlisted_untenanted_tables(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[str]:
    """Tables carrying no ``tenant_id`` that ``GLOBAL_TABLES`` does not name."""
    return [
        f"{table.name}: carries no tenant_id and is not on GLOBAL_TABLES"
        for table in _tenant_owned(metadata, global_tables)
        if _TENANT_ID not in table.c
    ]


def tenant_column_faults(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[str]:
    """A listed table carrying ``tenant_id`` (save the named ones), and a tenant-owned ``tenant_id`` that is
    nullable or has no foreign key to ``tenants``."""
    faults = [
        f"{name}: carries tenant_id while on GLOBAL_TABLES"
        for name, table in sorted(metadata.tables.items())
        if name in global_tables and _TENANT_ID in table.c and name not in _GLOBAL_TABLES_CARRYING_TENANT_ID
    ]
    for table in _tenant_owned(metadata, global_tables):
        if _TENANT_ID not in table.c:
            continue
        column = table.c[_TENANT_ID]
        if column.nullable:
            faults.append(f"{table.name}: tenant_id is nullable")
        if not any(fk.target_fullname == f"tenants.{_TENANT_ID}" for fk in column.foreign_keys):
            faults.append(f"{table.name}: tenant_id has no foreign key to tenants")
    return faults


def _hub_unique(column: sa.Column[object], seen: frozenset[sa.Column[object]] = frozenset()) -> bool:
    """A column marked ``hub_unique``, the table's autoincrement surrogate, or a foreign key onto either."""
    if column in seen:
        return False
    if column.info.get("hub_unique") or column.table.autoincrement_column is column:
        return True
    return any(_hub_unique(fk.column, seen | {column}) for fk in column.foreign_keys)


def _keys(table: sa.Table) -> list[tuple[str, list[sa.Column[object]]]]:
    keys: list[tuple[str, list[sa.Column[object]]]] = []
    if table.primary_key.columns:
        keys.append(("primary key", list(table.primary_key.columns)))
    for constraint in table.constraints:
        if isinstance(constraint, sa.UniqueConstraint):
            keys.append((f"unique {constraint.name or '<unnamed>'}", list(constraint.columns)))
    for index in table.indexes:
        if index.unique:
            keys.append((f"unique index {index.name}", list(index.columns)))
    return keys


def unscoped_keys(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[str]:
    """Tenant-owned keys holding neither ``tenant_id`` nor a hub-unique column — natural keys two tenants could
    both claim."""
    return [
        f"{table.name}: {kind} ({', '.join(column.name for column in columns)})"
        for table in _tenant_owned(metadata, global_tables)
        for kind, columns in _keys(table)
        if not any(column.name == _TENANT_ID or _hub_unique(column) for column in columns)
    ]


def _tenant_led(columns: list[sa.Column[object]]) -> bool:
    return bool(columns) and columns[0].name == _TENANT_ID


def _leading_columns(table: sa.Table) -> list[list[sa.Column[object]]]:
    led = [list(table.primary_key.columns)]
    led += [list(c.columns) for c in table.constraints if isinstance(c, sa.UniqueConstraint)]
    led += [[e] if isinstance(e := index.expressions[0], sa.Column) else [] for index in table.indexes]
    return led


def missing_tenant_index(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[str]:
    """Tenant-owned tables with no index, primary key or unique constraint led by ``tenant_id``."""
    return [
        f"{table.name}: no index led by tenant_id"
        for table in _tenant_owned(metadata, global_tables)
        if not any(_tenant_led(columns) for columns in _leading_columns(table))
    ]


def overlong_identifiers(metadata: sa.MetaData, global_tables: frozenset[str]) -> list[str]:
    """Constraint and index names on a tenant-owned table longer than Postgres keeps."""
    return [
        f"{table.name}: {name} is {len(name)} characters"
        for table in _tenant_owned(metadata, global_tables)
        for name in [*(c.name for c in table.constraints), *(i.name for i in table.indexes)]
        if isinstance(name, str) and len(name) > _IDENTIFIER_LIMIT and name not in _LONG_NAMES_KEPT
    ]


# --- the hub's own schema ----------------------------------------------------------------


def test_every_table_carries_its_tenant_or_is_on_the_global_list() -> None:
    assert _tenant_owned(schema.metadata, schema.GLOBAL_TABLES), "no tenant-owned table — the gate would be vacuous"
    assert unlisted_untenanted_tables(schema.metadata, schema.GLOBAL_TABLES) == []


def test_every_tenant_id_is_not_null_with_its_foreign_key_and_absent_from_global_tables() -> None:
    assert tenant_column_faults(schema.metadata, schema.GLOBAL_TABLES) == []


def test_every_tenant_owned_key_holds_the_tenant_or_a_hub_unique_column() -> None:
    faults = set(unscoped_keys(schema.metadata, schema.GLOBAL_TABLES))
    assert faults - _PENDING_KEYS.keys() == set(), "a natural key two tenants could both claim"
    assert _PENDING_KEYS.keys() - faults == set(), "a pending key is rescoped — delete its entry"


def test_every_tenant_owned_table_has_an_index_led_by_its_tenant() -> None:
    assert missing_tenant_index(schema.metadata, schema.GLOBAL_TABLES) == []


def test_every_tenant_owned_constraint_and_index_name_fits_the_identifier_limit() -> None:
    assert overlong_identifiers(schema.metadata, schema.GLOBAL_TABLES) == []
    declared = {index.name for table in schema.metadata.tables.values() for index in table.indexes}
    assert {name for name in _LONG_NAMES_KEPT if name in declared and len(name) > _IDENTIFIER_LIMIT} == set(
        _LONG_NAMES_KEPT
    ), "a kept long name is gone or now fits — delete its entry"


# --- each rule fails a deliberately broken schema -----------------------------------------


def _with_tenants() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table("tenants", metadata, sa.Column(_TENANT_ID, sa.String, primary_key=True))
    return metadata


def _tenant_column(table: str) -> sa.Column[str]:
    return sa.Column(_TENANT_ID, sa.String, sa.ForeignKey("tenants.tenant_id", name=f"fk_{table}_tenant_id"))


def _keyed_table(metadata: sa.MetaData, name: str, *extra: SchemaItem) -> sa.Table:
    table = sa.Table(
        name,
        metadata,
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            _TENANT_ID, sa.String, sa.ForeignKey("tenants.tenant_id", name=f"fk_{name}_tenant_id"), nullable=False
        ),
        *extra,
    )
    sa.Index(f"ix_{name}_tenant_id", table.c.tenant_id)
    return table


_GLOBAL = frozenset({"tenants"})


def test_the_gate_fails_a_table_with_no_tenant_id_that_is_not_listed() -> None:
    metadata = _with_tenants()
    sa.Table("notes", metadata, sa.Column("id", sa.Integer, primary_key=True))

    assert unlisted_untenanted_tables(metadata, _GLOBAL) == ["notes: carries no tenant_id and is not on GLOBAL_TABLES"]
    assert unlisted_untenanted_tables(metadata, _GLOBAL | {"notes"}) == []


def test_the_gate_fails_a_nullable_tenant_id() -> None:
    metadata = _with_tenants()
    table = sa.Table("notes", metadata, sa.Column("id", sa.Integer, primary_key=True), _tenant_column("notes"))
    assert table.c.tenant_id.nullable

    assert tenant_column_faults(metadata, _GLOBAL) == ["notes: tenant_id is nullable"]


def test_the_gate_fails_a_tenant_id_without_its_foreign_key_to_tenants() -> None:
    metadata = _with_tenants()
    sa.Table("parents", metadata, sa.Column("parent_id", sa.String, primary_key=True))
    sa.Table(
        "notes",
        metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(_TENANT_ID, sa.String, sa.ForeignKey("parents.parent_id"), nullable=False),
    )

    assert tenant_column_faults(metadata, _GLOBAL | {"parents"}) == ["notes: tenant_id has no foreign key to tenants"]


def test_the_gate_fails_a_listed_table_carrying_tenant_id_save_the_named_ones() -> None:
    metadata = _with_tenants()
    for name in ("audit_notes", "membership_facts", "tenant_deletions"):
        sa.Table(name, metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column(_TENANT_ID, sa.String))
    listed = _GLOBAL | {"audit_notes", "membership_facts", "tenant_deletions"}

    assert tenant_column_faults(metadata, listed) == ["audit_notes: carries tenant_id while on GLOBAL_TABLES"]


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        ("natural primary key", ["notes: primary key (slug)"]),
        ("natural unique constraint", ["notes: unique uq_notes_slug (slug)"]),
        ("natural unique index", ["notes: unique index ix_notes_slug (slug)"]),
        ("unique constraint holding tenant_id", []),
        ("primary key marked hub_unique", []),
        ("primary key a foreign key onto a hub-unique column", []),
    ],
)
def test_the_gate_fails_a_key_holding_neither_the_tenant_nor_a_hub_unique_column(
    shape: str, expected: list[str]
) -> None:
    metadata = _with_tenants()
    tenant = sa.Column(_TENANT_ID, sa.String, sa.ForeignKey("tenants.tenant_id"), nullable=False)
    sa.Table("graphs", metadata, sa.Column("graph_id", sa.String, primary_key=True, info={"hub_unique": True}))
    if shape == "natural primary key":
        sa.Table("notes", metadata, sa.Column("slug", sa.String, primary_key=True), tenant)
    elif shape == "primary key marked hub_unique":
        sa.Table(
            "notes", metadata, sa.Column("note_id", sa.String, primary_key=True, info={"hub_unique": True}), tenant
        )
    elif shape == "primary key a foreign key onto a hub-unique column":
        sa.Table("notes", metadata, sa.Column("graph_id", sa.ForeignKey("graphs.graph_id"), primary_key=True), tenant)
    else:
        table = sa.Table(
            "notes", metadata, sa.Column("id", sa.Integer, primary_key=True), sa.Column("slug", sa.String), tenant
        )
        if shape == "natural unique constraint":
            table.append_constraint(sa.UniqueConstraint("slug", name="uq_notes_slug"))
        elif shape == "natural unique index":
            sa.Index("ix_notes_slug", table.c.slug, unique=True)
        else:
            table.append_constraint(sa.UniqueConstraint(_TENANT_ID, "slug", name="uq_notes_tenant_slug"))

    assert unscoped_keys(metadata, _GLOBAL | {"graphs"}) == expected


@pytest.mark.parametrize(
    ("leading", "expected"),
    [
        ("no tenant-led index", ["notes: no index led by tenant_id"]),
        ("index with tenant_id second", ["notes: no index led by tenant_id"]),
        ("tenant-led index", []),
        ("tenant-led primary key", []),
        ("tenant-led unique constraint", []),
    ],
)
def test_the_gate_fails_a_table_with_no_index_led_by_its_tenant(leading: str, expected: list[str]) -> None:
    metadata = _with_tenants()
    table = sa.Table(
        "notes",
        metadata,
        sa.Column(_TENANT_ID, sa.String, sa.ForeignKey("tenants.tenant_id"), nullable=False),
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("at", sa.Integer, nullable=False),
    )
    if leading == "index with tenant_id second":
        sa.Index("ix_notes_at_tenant", table.c.at, table.c.tenant_id)
    elif leading == "tenant-led index":
        sa.Index("ix_notes_tenant_at", table.c.tenant_id, table.c.at)
    elif leading == "tenant-led primary key":
        table.append_constraint(sa.PrimaryKeyConstraint(_TENANT_ID, "slug", name="pk_notes"))
    elif leading == "tenant-led unique constraint":
        table.append_constraint(sa.UniqueConstraint(_TENANT_ID, "slug", name="uq_notes_tenant_slug"))

    assert missing_tenant_index(metadata, _GLOBAL) == expected


def test_the_gate_fails_a_constraint_or_index_name_past_the_identifier_limit() -> None:
    metadata = _with_tenants()
    fits = "ix_notes_" + "a" * (_IDENTIFIER_LIMIT - len("ix_notes_"))
    too_long = "uq_notes_" + "b" * (_IDENTIFIER_LIMIT + 1 - len("uq_notes_"))
    _keyed_table(
        metadata,
        "notes",
        sa.Column("slug", sa.String),
        sa.Column("at", sa.Integer),
        sa.UniqueConstraint(_TENANT_ID, "slug", name=too_long),
    )
    sa.Index(fits, metadata.tables["notes"].c.at)

    assert overlong_identifiers(metadata, _GLOBAL) == [f"notes: {too_long} is 64 characters"]


def test_a_well_formed_tenant_owned_table_passes_every_rule() -> None:
    metadata = _with_tenants()
    _keyed_table(metadata, "notes", sa.Column("slug", sa.String), sa.UniqueConstraint(_TENANT_ID, "slug", name="uq_n"))
    rules: list[Rule] = [
        unlisted_untenanted_tables,
        tenant_column_faults,
        unscoped_keys,
        missing_tenant_index,
        overlong_identifiers,
    ]

    assert {rule.__name__: rule(metadata, _GLOBAL) for rule in rules} == {rule.__name__: [] for rule in rules}
