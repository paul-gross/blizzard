"""Tests proving the ``20261008_1500_scope_surrogate`` revision."""

from __future__ import annotations

import logging
import re
import warnings
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError, SAWarning

from blizzard.foundation.ids import Id
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.migration_support_store_migrations import _schema_snapshot
from tests.migration_support_tenant_carry_over_migration import (
    _BACKFILL,
    _BEFORE,
    _LINKS_AT_HEAD,
    _foreign_key_violations,
    _rows_each_update_changed,
    _seed_every_tenant_owned_table,
    _store,
    _tables,
    _tenant_rows,
)
from tests.migration_support_tenant_keys_migration import _SECOND_TENANT, _T0, _snapshot, _tenant_ids
from tests.migration_support_tenant_tighten_migration import _index_names, _seeded_at_the_keys
from tests.support import hub_store_connections

pytestmark = [pytest.mark.component]

_TIGHTEN = "20261008_1400_tenant_tighten"

_SURROGATE = "20261008_1500_scope_surrogate"

# Each child as the revision links it: (table, the slug column it drops or keeps, the id column it gains).
_CHILDREN = (
    ("scope_lifecycle_facts", "slug", "scope_id"),
    ("routines", "default_scope_slug", "default_scope_id"),
    ("routine_scopes", "scope_slug", "scope_id"),
    ("work_item_runs", "scope_slug", "scope_id"),
    ("findings", "scope_slug", "scope_id"),
    ("finding_sets", "scope_slug", "scope_id"),
)

_LABELLED = frozenset({"findings", "finding_sets"})


def _seeded_at_the_tighten(tmp_path: Path) -> tuple[str, sa.Engine]:
    """A store holding one row in every tenant-owned table in its one tenant, each scope link naming the seeded scope."""
    db_url, engine = _seeded_at_the_keys(tmp_path)
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_TIGHTEN)
    return db_url, engine


def _rows(engine: sa.Engine) -> dict[str, list[str]]:
    """Every row of every table as its columns by name, in a stable order — a column's position is not compared."""
    with engine.connect() as conn:
        return {
            table: sorted(
                repr(sorted(row._mapping.items()))
                for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))
            )
            for table in sa.inspect(engine).get_table_names()
            if table != "alembic_version"
        }


def _add_second_tenant(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text(f"INSERT INTO tenants VALUES ('{_SECOND_TENANT}', 'second', :at, 'migration')"), {"at": _T0}
        )


def _copy_scope_into_second_tenant(engine: sa.Engine, slug: str, *, named: str) -> None:
    """Copy the seeded scope and every child row as rows of the second tenant: the scope under ``slug``, each child
    naming ``named``."""
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scopes (slug, description, created_at, revision, tenant_id)"
                " SELECT :slug, description, created_at, revision, :tenant FROM scopes"
            ),
            {"slug": slug, "tenant": _SECOND_TENANT},
        )
        for table, column, _ in _CHILDREN:
            inspector = sa.inspect(conn)
            columns = [c["name"] for c in inspector.get_columns(table) if c["name"] != "id"]
            distinct = {
                *inspector.get_pk_constraint(table)["constrained_columns"],
                *(name for u in inspector.get_unique_constraints(table) for name in u["column_names"]),
                *(name for i in inspector.get_indexes(table) if i["unique"] for name in i["column_names"]),
            }
            values = [
                ":named"
                if name == column
                else ":tenant"
                if name == "tenant_id"
                else f"{name} || '-2'"
                if name in distinct
                else name
                for name in columns
            ]
            conn.execute(
                sa.text(f"INSERT INTO {table} ({', '.join(columns)}) SELECT {', '.join(values)} FROM {table}"),
                {"named": named, "tenant": _SECOND_TENANT},
            )


def _scope_ids(engine: sa.Engine) -> dict[tuple[str, str], str]:
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT tenant_id, slug, scope_id FROM scopes"))
        return {(tenant, slug): scope_id for tenant, slug, scope_id in rows}


def _name_lost_slugs(engine: sa.Engine, tenant: str) -> dict[str, str]:
    """Point every row ``tenant`` holds in each child at a slug no scope holds, one per child; each child to its slug."""
    lost = {table: f"lost-{n}" for n, (table, _, _) in enumerate(_CHILDREN)}
    with engine.begin() as conn:
        for table, column, _ in _CHILDREN:
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :slug WHERE tenant_id = :tenant"),
                {"slug": lost[table], "tenant": tenant},
            )
    return lost


def _named(engine: sa.Engine, table: str, column: str) -> list[tuple[str, str]]:
    """Each row of ``table`` as its tenant and the slug ``column`` names, ``column`` reaching ``scopes`` when it holds
    the id."""
    with engine.connect() as conn:
        if column in {"slug", "scope_slug", "default_scope_slug"}:
            query = f"SELECT tenant_id, {column} FROM {table}"
        else:
            query = (
                f"SELECT c.tenant_id, s.slug FROM {table} c LEFT JOIN scopes s"
                f" ON s.tenant_id = c.tenant_id AND s.scope_id = c.{column}"
            )
        return sorted(tuple(row) for row in conn.execute(sa.text(query)))


def _revision_module() -> ModuleType:
    script = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_SURROGATE)
    assert script is not None
    return script.module


def _rows_in_any_tenant(engine: sa.Engine) -> dict[str, list[str]]:
    """``_rows`` but ``tenants``, with the tenant and scope ids masked: comparable across two stores minting their own."""
    [tenant] = _tenant_ids(engine)
    masked = [(tenant, "<tenant>"), *((scope_id, "<scope>") for scope_id in _scope_ids(engine).values())]
    rows = {table: table_rows for table, table_rows in _rows(engine).items() if table != "tenants"}
    for old, new in masked:
        rows = {table: sorted(row.replace(old, new) for row in table_rows) for table, table_rows in rows.items()}
    return rows


def _relinked_part_way(module: ModuleType, conn: sa.Connection) -> None:
    """Stand-ins staged, ids minted, two children carried and one of them taken off the slug."""
    links = {link[0]: link for link in module._LINKS}
    module._stage_stand_ins(conn)
    module._mint_scope_ids(conn)
    module._carry_ids(conn, links["findings"])
    module._carry_ids(conn, links["routine_scopes"])
    module._detach(conn, links["findings"])


def _seated_part_way(module: ModuleType, conn: sa.Connection) -> None:
    """Every step through the stand-ins seated in the rekeyed ``scopes``, one child attached by its id."""
    module._stage_stand_ins(conn)
    module._mint_scope_ids(conn)
    for link in module._LINKS:
        module._carry_ids(conn, link)
    for link in module._LINKS:
        module._detach(conn, link)
    module._rekey_scopes(conn)
    module._seat_stand_ins(conn)
    module._attach(conn, module._LINKS[0])


def _seed_past_a_batch(engine: sa.Engine, count: int) -> None:
    """``count`` scopes spread over two tenants, so a page boundary falls inside one tenant; one lifecycle fact and one
    routine link (a two-column key, spread over several routines) naming each."""
    _add_second_tenant(engine)
    [first] = [tenant for tenant in _tenant_ids(engine) if tenant != _SECOND_TENANT]
    scopes = [{"slug": f"s{n:05d}", "tenant": first if n % 2 else _SECOND_TENANT, "at": _T0} for n in range(count)]
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO scopes (slug, description, created_at, revision, tenant_id)"
                " VALUES (:slug, 'd', :at, 1, :tenant)"
            ),
            scopes,
        )
        conn.execute(
            sa.text(
                "INSERT INTO scope_lifecycle_facts (slug, retired, set_at, set_by, tenant_id)"
                " VALUES (:slug, 0, :at, 'operator', :tenant)"
            ),
            scopes,
        )
        conn.execute(
            sa.text("INSERT INTO routine_scopes (routine_id, scope_slug, tenant_id) VALUES (:routine, :slug, :tenant)"),
            [{**scope, "routine": f"rtn_{n % 3}"} for n, scope in enumerate(scopes)],
        )


_SURROGATE = "20261008_1500_scope_surrogate"

_CARRY_OVER_SECOND_TENANT = "ten_01J9Z3M0P8QK7V2S4W6X8Y0A1C"

# Rows added to the one-row-per-table seed, each a copy of that table's seeded row with these columns changed: a second
# scope holding children of every kind beside the first's, a second secret with its own referrers, a second question
# with a delivery and no answer, locks in two namespaces, and the hub source's counter part way along.
_LINKED_SEED: tuple[tuple[str, dict[str, object]], ...] = (
    ("scopes", {"slug": "beta"}),
    ("scope_lifecycle_facts", {"id": 2, "slug": "beta"}),
    ("routines", {"routine_id": "rt_2", "name": "name-2", "default_scope_slug": "beta"}),
    ("routine_scopes", {"scope_slug": "beta"}),
    ("routine_scopes", {"routine_id": "rt_2", "scope_slug": "beta"}),
    ("work_items", {"work_item_id": "wi_2", "source": "hub", "ref": "41"}),
    ("work_item_runs", {"work_item_id": "wi_2", "scope_slug": "beta"}),
    ("findings", {"finding_id": "fd_2", "scope_slug": "beta"}),
    ("secrets", {"name": "forge-token"}),
    ("secret_lifecycle_facts", {"id": 2, "name": "forge-token"}),
    ("work_sources", {"name": "tracker", "locator": "locator-2", "secret_name": "forge-token"}),
    ("questions", {"question_id": "qn_2"}),
    ("answer_deliveries", {"id": 2, "question_id": "qn_2"}),
    ("answer_deliveries", {"id": 3}),
    ("keyed_locks", {"key": "key-2"}),
    ("keyed_locks", {"namespace": "chunk", "key": "key-1"}),
    ("work_item_sequence", {"source": "hub", "next_ref": 42}),
)

# Each link the carried-over rows hold, as (row, the parent's natural key) — read the way the pre-tenant store keys it,
# and the way the store at head does: through its rescoped key, within the row's own tenant.
_LINKS_BEFORE = {
    "scope_lifecycle_facts": "SELECT id, slug FROM scope_lifecycle_facts",
    "routines": "SELECT routine_id, default_scope_slug FROM routines",
    "routine_scopes": "SELECT routine_id, scope_slug FROM routine_scopes",
    "work_item_runs": "SELECT work_item_id, scope_slug FROM work_item_runs",
    "findings": "SELECT finding_id, scope_slug FROM findings",
    "finding_sets": "SELECT finding_set_id, scope_slug FROM finding_sets",
    "secret_lifecycle_facts": "SELECT id, name FROM secret_lifecycle_facts",
    "work_sources": "SELECT name, secret_name FROM work_sources",
    "repositories": "SELECT name, secret_name FROM repositories",
    "question_answers": "SELECT question_id, answer FROM question_answers",
    "answer_deliveries": "SELECT id, question_id FROM answer_deliveries",
    "keyed_locks": "SELECT namespace, key FROM keyed_locks",
    "work_item_sequence": "SELECT source, next_ref FROM work_item_sequence",
    "work_items": "SELECT source, ref FROM work_items",
}


def _seed_linked_rows(engine: sa.Engine) -> None:
    """Over the one-row-per-table seed, the rows of ``_LINKED_SEED``."""
    with engine.begin() as conn:
        for table, changes in _LINKED_SEED:
            columns = [f'"{c["name"]}"' for c in sa.inspect(conn).get_columns(table)]
            values = [
                f":{c['name']}" if c["name"] in changes else f'"{c["name"]}"'
                for c in sa.inspect(conn).get_columns(table)
            ]
            conn.execute(
                sa.text(
                    f"INSERT INTO {table} ({', '.join(columns)})"
                    f" SELECT {', '.join(values)} FROM {table} ORDER BY rowid LIMIT 1"
                ),
                changes,
            )


def _links(engine: sa.Engine, reads: dict[str, str]) -> dict[str, list[tuple[object, ...]]]:
    with engine.connect() as conn:
        return {table: sorted(tuple(row) for row in conn.execute(sa.text(read))) for table, read in reads.items()}


def _carried_over_with_links(tmp_path: Path, revision: str) -> tuple[MigrationRunner, sa.Engine]:
    """A pre-tenant store holding a row in every tenant-owned table and the links of ``_LINKED_SEED``, migrated to
    ``revision``."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    runner.upgrade(_BEFORE)
    _seed_every_tenant_owned_table(engine)
    _seed_linked_rows(engine)
    runner.upgrade(revision)
    return runner, engine


def _rows_by_column(engine: sa.Engine) -> dict[str, list[str]]:
    """Every row of every table as its columns by name, in a stable order — a rebuilt table's column order is not
    compared."""
    with engine.connect() as conn:
        return {
            table: sorted(
                repr(sorted(row._mapping.items()))
                for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))
            )
            for table in _tables(engine)
        }


def test_the_upgrade_links_every_row_to_its_own_tenants_scope_by_a_minted_id(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    try:
        [first] = _tenant_ids(engine)
        with engine.connect() as conn:
            slug = conn.execute(sa.text("SELECT slug FROM scopes")).scalar_one()
        _add_second_tenant(engine)
        _copy_scope_into_second_tenant(engine, "second-slug", named="second-slug")
        counts = {table: len(rows) for table, rows in _rows(engine).items()}

        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_SURROGATE)

        assert {table: len(rows) for table, rows in _rows(engine).items()} == counts
        ids = _scope_ids(engine)
        assert set(ids) == {(first, slug), (_SECOND_TENANT, "second-slug")}
        for scope_id in ids.values():
            parsed = Id.parse(scope_id)
            assert parsed is not None and parsed.prefix == "scp"
            minted_at = parsed.minted_at
            assert minted_at is not None and abs(minted_at - _T0) < timedelta(milliseconds=1)
        with engine.connect() as conn:
            for table, slug_column, id_column in _CHILDREN:
                linked = conn.execute(sa.text(f"SELECT tenant_id, {id_column} FROM {table}")).all()
                assert sorted(linked) == sorted(
                    [(first, ids[(first, slug)]), (_SECOND_TENANT, ids[(_SECOND_TENANT, "second-slug")])]
                ), table
                columns = {c["name"] for c in sa.inspect(conn).get_columns(table)}
                assert (slug_column in columns) == (table in _LABELLED), table
            labels = conn.execute(sa.text("SELECT tenant_id, scope_slug FROM findings")).all()
            assert sorted(labels) == sorted([(first, slug), (_SECOND_TENANT, "second-slug")])
    finally:
        engine.dispose()


def test_after_the_upgrade_a_slug_is_unique_within_its_tenant_and_every_link_is_keyed_by_the_id(
    tmp_path: Path,
) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    try:
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_SURROGATE)
        _add_second_tenant(engine)
        inspector = sa.inspect(engine)

        assert inspector.get_pk_constraint("scopes") == {"name": "pk_scopes", "constrained_columns": ["scope_id"]}
        assert {u["name"]: u["column_names"] for u in inspector.get_unique_constraints("scopes")} == {
            "uq_scopes_tenant_slug": ["tenant_id", "slug"]
        }
        assert "ix_scopes_tenant_id" not in _index_names(engine, "scopes")
        assert inspector.get_pk_constraint("routine_scopes") == {
            "name": "pk_routine_scopes",
            "constrained_columns": ["routine_id", "scope_id"],
        }
        assert "ix_routine_scopes_tenant_scope_id" in _index_names(engine, "routine_scopes")
        assert not {"ix_routine_scopes_tenant_scope_slug", "ix_routine_scopes_tenant_id"} & _index_names(
            engine, "routine_scopes"
        )
        for table, _, id_column in _CHILDREN:
            onto_scopes = [fk for fk in inspector.get_foreign_keys(table) if fk["referred_table"] == "scopes"]
            assert [(fk["name"], fk["constrained_columns"], fk["referred_columns"]) for fk in onto_scopes] == [
                (f"fk_{table}_scopes", [id_column], ["scope_id"])
            ], table
            assert not next(c for c in inspector.get_columns(table) if c["name"] == id_column)["nullable"], table

        insert = sa.text(
            "INSERT INTO scopes (scope_id, slug, description, created_at, revision, tenant_id)"
            " VALUES (:scope_id, 'slug-1', 'd', :at, 1, :tenant)"
        )
        with engine.begin() as conn:
            conn.execute(insert, {"scope_id": "scp_second", "at": _T0, "tenant": _SECOND_TENANT})
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(insert, {"scope_id": "scp_third", "at": _T0, "tenant": _SECOND_TENANT})
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("stopped", "copying"),
    [(_relinked_part_way, "scopes"), (_seated_part_way, "finding_sets")],
    ids=["relinked", "seated"],
)
def test_a_rerun_over_a_store_relinked_part_way_finishes_with_the_same_ids(
    tmp_path: Path, stopped: Callable[[ModuleType, sa.Connection], None], copying: str
) -> None:
    """Re-entry: ids already minted and carried are kept, stand-ins already staged or seated are neither lost nor
    doubled, a child already taken off the slug is skipped, the copy a stopped batch left behind is cleared, and the
    store ends as a single run leaves it."""
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        [tenant] = _tenant_ids(engine)
        _name_lost_slugs(engine, tenant)
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            stopped(_revision_module(), conn)
        minted = _scope_ids(engine)
        with engine.begin() as conn:
            # the leftover of a stopped copy of a table the rerun still reshapes
            conn.execute(sa.text(f"CREATE TABLE _alembic_tmp_{copying} (slug VARCHAR)"))
        assert runner.current_revision() == _TIGHTEN

        runner.upgrade(_SURROGATE)

        assert runner.current_revision() == _SURROGATE
        assert {key: _scope_ids(engine)[key] for key in minted} == minted
        (tmp_path / "reference").mkdir()
        reference_url, reference = _seeded_at_the_tighten(tmp_path / "reference")
        try:
            [reference_tenant] = _tenant_ids(reference)
            _name_lost_slugs(reference, reference_tenant)
            migration_runner(HubConfig(root=tmp_path / "reference", db_url=reference_url)).upgrade(_SURROGATE)
            assert _snapshot(engine) == _snapshot(reference)
            assert _rows_in_any_tenant(engine) == _rows_in_any_tenant(reference)
        finally:
            reference.dispose()
    finally:
        engine.dispose()


def test_the_upgrade_mints_and_carries_ids_in_bounded_batches_across_more_than_one(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        batch = _revision_module()._BATCH_ROWS
        count = 2 * batch + 7
        _seed_past_a_batch(engine, count)

        with _rows_each_update_changed() as updates:
            runner.upgrade(_SURROGATE)

        ids = _scope_ids(engine)
        assert len(set(ids.values())) == len(ids) == count + 1
        with engine.connect() as conn:
            for table, id_column in (("scope_lifecycle_facts", "scope_id"), ("routine_scopes", "scope_id")):
                linked = conn.execute(sa.text(f"SELECT {id_column} FROM {table}")).scalars().all()
                assert len(linked) == count + 1 and set(linked) == set(ids.values()), table
                filled = [rows for updated, rows in updates if updated == table]
                assert len(filled) >= 3 and max(filled) <= batch and sum(filled) == count + 1, table
    finally:
        engine.dispose()


def test_the_upgrade_mints_a_stand_in_scope_for_every_slug_a_row_names_but_its_tenant_does_not_hold(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        [first] = _tenant_ids(engine)
        with engine.connect() as conn:
            slug = conn.execute(sa.text("SELECT slug FROM scopes")).scalar_one()
        _add_second_tenant(engine)
        _copy_scope_into_second_tenant(engine, "second-slug", named=slug)
        lost = _name_lost_slugs(engine, first)
        named = {table: _named(engine, table, column) for table, column, _ in _CHILDREN}
        counts = {table: len(rows) for table, rows in _rows(engine).items()}

        with warnings.catch_warnings(), caplog.at_level(logging.INFO, logger="alembic.runtime.migration"):
            warnings.simplefilter("error", SAWarning)
            runner.upgrade(_SURROGATE)

        assert runner.current_revision() == _SURROGATE
        stand_ins = {(first, name) for name in lost.values()} | {(_SECOND_TENANT, slug)}
        assert "minted 7 stand-in scopes" in caplog.text
        assert {table: len(rows) for table, rows in _rows(engine).items()} == {
            **counts,
            "scopes": counts["scopes"] + len(stand_ins),
        }
        assert not sa.inspect(engine).has_table("scope_stand_ins")
        ids = _scope_ids(engine)
        assert set(ids) == {(first, slug), (_SECOND_TENANT, "second-slug"), *stand_ins}
        with engine.connect() as conn:
            minted = conn.execute(
                sa.text("SELECT tenant_id, slug, scope_id, description, created_at, revision FROM scopes")
            ).all()
        for tenant, name, scope_id, description, created_at, revision in minted:
            if (tenant, name) not in stand_ins:
                continue
            parsed = Id.parse(scope_id)
            assert (
                parsed is not None and parsed.prefix == "scp" and parsed.minted_at == datetime(1970, 1, 1, tzinfo=UTC)
            )
            assert (description, revision) == ("", 1)
            assert datetime.fromisoformat(str(created_at)).replace(tzinfo=UTC) == datetime(1970, 1, 1, tzinfo=UTC)
        assert len(set(ids.values())) == len(ids)
        for table, _, id_column in _CHILDREN:
            assert _named(engine, table, id_column) == named[table], table
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_deletes_the_stand_ins_and_restores_the_store_exactly(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        [tenant] = _tenant_ids(engine)
        lost = _name_lost_slugs(engine, tenant)
        snapshot, rows = _snapshot(engine), _rows(engine)

        runner.upgrade(_SURROGATE)
        assert {(tenant, name) for name in lost.values()} <= set(_scope_ids(engine))
        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            runner.downgrade(_TIGHTEN)

        assert runner.current_revision() == _TIGHTEN
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_the_downgrade_refuses_a_store_holding_two_tenants_and_changes_nothing(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_SURROGATE)
        [first] = _tenant_ids(engine)
        _add_second_tenant(engine)
        snapshot, rows = _snapshot(engine), _rows(engine)

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_TIGHTEN)

        assert first in str(refused.value) and _SECOND_TENANT in str(refused.value)
        assert runner.current_revision() == _SURROGATE
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_with_one_tenant_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    db_url, engine = _seeded_at_the_tighten(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        snapshot, rows = _snapshot(engine), _rows(engine)

        runner.upgrade(_SURROGATE)
        assert _snapshot(engine) != snapshot
        assert re.fullmatch(r"scp_[0-9A-Z]{26}", next(iter(_scope_ids(engine).values())))
        with warnings.catch_warnings():
            warnings.simplefilter("error", SAWarning)
            runner.downgrade(_TIGHTEN)

        assert runner.current_revision() == _TIGHTEN
        assert _snapshot(engine) == snapshot
        assert _rows(engine) == rows
    finally:
        engine.dispose()


def test_reattaching_a_table_missing_only_its_slug_foreign_key_restores_it_on_sqlite(tmp_path: Path) -> None:
    """Nothing but the missing key calls for a copy, so restoring it must still copy the table on SQLite."""
    _, engine = _seeded_at_the_tighten(tmp_path)
    module = _revision_module()
    table, slug = "scope_lifecycle_facts", "slug"
    try:
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            reflected = module._reflected(conn, table)
            module._discard_foreign_key(reflected, slug)
            with module.op.batch_alter_table(table, recreate="always", copy_from=reflected):
                pass
            assert module._scope_foreign_keys(conn, table, slug, "slug") == []

            module._reattach(conn, next(link for link in module._LINKS if link[0] == table))

            assert module._scope_foreign_keys(conn, table, slug, "slug") == [None]
    finally:
        engine.dispose()


def test_a_carried_over_store_keeps_every_link_through_the_rescoped_keys(tmp_path: Path) -> None:
    runner, engine = _carried_over_with_links(tmp_path, _BEFORE)
    try:
        links = _links(engine, _LINKS_BEFORE)
        assert {slug for _, slug in links["routine_scopes"]} == {"slug-1", "beta"}
        assert {secret for _, secret in links["work_sources"]} == {"name-1", "forge-token"}

        runner.upgrade("head")

        assert _links(engine, _LINKS_AT_HEAD) == links
        assert _foreign_key_violations(engine) == []
        [(tenant_id, *_)] = _tenant_rows(engine)
        with engine.connect() as conn:
            assert conn.execute(
                sa.text("SELECT tenant_id, next_ref FROM work_item_sequence WHERE source = 'hub'")
            ).one() == (tenant_id, 42)
        assert WorkItemStore(hub_store_connections(engine)).allocate_ref("hub") == "42"
    finally:
        engine.dispose()


def test_the_tightened_keys_downgrade_to_the_backfill_only_once_one_tenant_remains_and_then_exactly(
    tmp_path: Path,
) -> None:
    runner, engine = _carried_over_with_links(tmp_path, _BACKFILL)
    try:
        [first] = [tenant_id for tenant_id, *_ in _tenant_rows(engine)]
        backfilled = _schema_snapshot(engine, exemptions={}, name_exemptions={}), _rows_by_column(engine)
        runner.upgrade(_SURROGATE)
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO tenants VALUES (:tenant, 'second', :at, 'migration')"),
                {"tenant": _CARRY_OVER_SECOND_TENANT, "at": _T0},
            )
        tightened = _schema_snapshot(engine, exemptions={}, name_exemptions={}), _rows_by_column(engine)

        with pytest.raises(RuntimeError, match="delete every tenant but one before downgrading") as refused:
            runner.downgrade(_BACKFILL)

        assert first in str(refused.value) and _CARRY_OVER_SECOND_TENANT in str(refused.value)
        assert runner.current_revision() == _SURROGATE
        assert (_schema_snapshot(engine, exemptions={}, name_exemptions={}), _rows_by_column(engine)) == tightened

        with engine.begin() as conn:
            conn.execute(
                sa.text("DELETE FROM tenants WHERE tenant_id = :tenant"), {"tenant": _CARRY_OVER_SECOND_TENANT}
            )
        runner.downgrade(_BACKFILL)

        assert runner.current_revision() == _BACKFILL
        assert (_schema_snapshot(engine, exemptions={}, name_exemptions={}), _rows_by_column(engine)) == backfilled
    finally:
        engine.dispose()
