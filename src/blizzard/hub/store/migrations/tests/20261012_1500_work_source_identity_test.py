"""The work-source identity revision keys ``work_sources`` by a minted ``wsrc_`` id with a slug unique within its tenant
and a display name, and every reference — lifecycle facts, project source links and their facts, annotation facts, the
work refs, and the change rows of the two kinds — holds the id. Rows survive; a name that is not a slug is given a
derived one, never ``hub``; a ``hub`` work ref stays ``hub`` and one naming no source stays as stored; a name no source
holds in a table with a foreign key refuses the upgrade; a run stopped after any step finishes as a single run does; the
fills page in bounded batches; and the downgrade restores the name keys.

Seeded from the store's reflected shape at the pinned revisions rather than ``src/blizzard/hub/store/schema.py``, whose
tables keep changing shape after this revision."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import sqlalchemy as sa
from alembic.script import ScriptDirectory
from sqlalchemy.exc import IntegrityError

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from tests.migration_support_project_columns_migration import _insert, _query, _run_step
from tests.migration_support_store_migrations import _schema_snapshot

pytestmark = [pytest.mark.component]

_BEFORE = "20261012_1100_runner_bindings"

_IDENTITY = "20261012_1500_work_source_identity"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_NOW = datetime(2026, 10, 12, 15, 0, tzinfo=UTC)

_SECOND = "ten_second"

_LONG = "x" * 130

_SOURCE_ID = re.compile(r"^wsrc_[0-9A-HJKMNP-TV-Z]{26}$")

#: A work ref left behind by a source that never existed in its tenant.
_STRANDED = "acme-tail"

_WORK_REFS = ("chunk_work_refs", "work_item_closures", "close_intents")


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_IDENTITY)
    assert revision is not None
    return revision.module


def _source(tenant: str, name: str, n: int) -> dict[str, object]:
    return {
        "tenant_id": tenant,
        "name": name,
        "provider": "github",
        "locator": f"acme/widget-{n}",
        "annotate": True,
        "secret_name": "tok",
        "revision": 1,
        "created_at": _T0 + timedelta(minutes=n),
        "created_by": "migration",
    }


@contextmanager
def _before(
    tmp_path: Path, names: tuple[str, ...], *, second: tuple[str, ...] = ("blizzard",)
) -> Iterator[tuple[MigrationRunner, sa.Engine, str, str]]:
    """A store at the revision before this one holding two tenants. The store's own holds one source per name in
    ``names`` — each with a lifecycle fact, a link from its project with a link fact, an annotation fact, a work ref,
    closure and close intent on one chunk, and the change rows of the source and its link — plus a ``hub`` work ref and
    one naming no source; the second tenant holds ``second`` with a change row each. The store's own tenant's id and
    its project's."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_BEFORE)
    engine = create_engine_from_url(db_url)
    try:
        _insert(engine, "tenants", [{"tenant_id": _SECOND, "name": "second", "created_by": "migration"}])
        (tenant,) = _query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id != :s", s=_SECOND)[0]
        (project,) = _query(engine, "SELECT project_id FROM projects WHERE tenant_id = :t", t=tenant)[0]
        t = {"tenant_id": tenant}
        _insert(engine, "secrets", [{**t, "name": "tok"}, {"tenant_id": _SECOND, "name": "tok"}])
        _insert(engine, "chunks", [{**t, "chunk_id": "ch_00000", "minted_at": _T0, "project_id": project}])
        _insert(engine, "work_sources", [_source(tenant, name, n) for n, name in enumerate(names)])
        _insert(engine, "work_sources", [_source(_SECOND, name, n) for n, name in enumerate(second)])
        _insert(engine, "work_source_lifecycle_facts", [{**t, "name": name, "retired": True} for name in names])
        _insert(engine, "project_source_links", [{**t, "project_id": project, "source_name": n} for n in names])
        _insert(
            engine,
            "project_source_link_facts",
            [{**t, "project_id": project, "source_name": n, "linked": True} for n in names],
        )
        _insert(engine, "forge_annotation_facts", [{**t, "source_name": n, "annotating": True} for n in names])
        refs = [*names, "hub", _STRANDED]
        chunk = {**t, "chunk_id": "ch_00000"}
        _insert(engine, "chunk_work_refs", [{**chunk, "source": s, "ref": str(n)} for n, s in enumerate(refs)])
        _insert(
            engine,
            "work_item_closures",
            [{**chunk, "source": s, "ref": str(n), "outcome": "closed"} for n, s in enumerate(refs)],
        )
        _insert(engine, "close_intents", [{**chunk, "source": s, "ref": str(n)} for n, s in enumerate(refs)])
        changes = [
            {"tenant_id": tn, "record_kind": kind, "record_key": name, "diff": '[{"field": "locator"}]'}
            for tn, owned in ((tenant, names), (_SECOND, second))
            for name in owned
            for kind in ("work_source", "project_source_link")
        ]
        changes += [
            {**t, "record_kind": "secret", "record_key": "tok", "diff": "[]"},
            {**t, "record_kind": "repository", "record_key": names[0], "diff": "[]"},
        ]
        _insert(engine, "config_changes", changes)
        runner.upgrade(_BEFORE)
        yield runner, engine, tenant, project
    finally:
        engine.dispose()


def _ids(engine: sa.Engine) -> dict[tuple[str, str], str]:
    """Each source's id by ``(tenant, slug)``."""
    return {
        (t, slug): id_ for id_, slug, t in _query(engine, "SELECT work_source_id, slug, tenant_id FROM work_sources")
    }


def _rows(engine: sa.Engine, table: str) -> list[dict[str, Any]]:
    """Every row of ``table`` as its columns by name — a column's position is not compared."""
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))]


def _labelled(value: object, labels: dict[str, str]) -> object:
    if not isinstance(value, str):
        return value
    for minted, label in labels.items():
        value = value.replace(minted, label)
    return value


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The store with every minted tenant, project and source id read as a label: its schema and every row."""
    minted = _query(engine, "SELECT tenant_id, project_id FROM projects ORDER BY created_at LIMIT 1")
    labels = {minted[0][0]: "tenant", minted[0][1]: "project"}
    if "work_source_id" in {c["name"] for c in sa.inspect(engine).get_columns("work_sources")}:
        for id_, tenant_id, slug in _query(engine, "SELECT work_source_id, tenant_id, slug FROM work_sources"):
            labels[id_] = f"source-{labels.get(tenant_id, tenant_id)}-{slug}"
    rows = {
        table: sorted(repr(sorted((c, _labelled(v, labels)) for c, v in row.items())) for row in _rows(engine, table))
        for table in sorted(sa.inspect(engine).get_table_names())
    }
    return _schema_snapshot(engine, exemptions={}, name_exemptions={}), rows


def test_every_name_becomes_a_slug_and_a_display_name_and_none_becomes_hub(tmp_path: Path) -> None:
    names = ("blizzard", "Odd Name", "odd-name", " padded ", _LONG, "!!!", "HUB")
    with _before(tmp_path, names) as (runner, engine, tenant, _project):
        runner.upgrade(_IDENTITY)

        held = dict(_query(engine, "SELECT slug, name FROM work_sources WHERE tenant_id = :t", t=tenant))
        assert held == {
            "blizzard": "blizzard",
            "odd-name": "odd-name",
            "odd-name-2": "Odd Name",
            "padded": "padded",
            "x" * 64: "x" * 128,
            "work-source": "!!!",
            "hub-2": "HUB",
        }
        assert all(_SOURCE_ID.match(id_) for id_ in _ids(engine).values())
        assert len(set(_ids(engine).values())) == len(_ids(engine))


def test_every_reference_holds_the_id_a_hub_ref_stays_and_a_stranded_ref_is_kept(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with _before(tmp_path, ("blizzard", "Odd Name")) as (runner, engine, tenant, project):
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            runner.upgrade(_IDENTITY)

        ids = _ids(engine)
        blizzard, odd = ids[(tenant, "blizzard")], ids[(tenant, "odd-name")]
        for table in (
            "work_source_lifecycle_facts",
            "project_source_links",
            "project_source_link_facts",
            "forge_annotation_facts",
        ):
            assert {r[0] for r in _query(engine, f"SELECT work_source_id FROM {table}")} == {blizzard, odd}
            columns = {c["name"] for c in sa.inspect(engine).get_columns(table)}
            assert not {"name", "source_name"} & columns
        for table in _WORK_REFS:
            assert {r[0] for r in _query(engine, f"SELECT source FROM {table}")} == {blizzard, odd, "hub", _STRANDED}
        assert _STRANDED in caplog.text
        assert _query(engine, "SELECT project_id FROM project_source_links GROUP BY project_id") == [(project,)]


def test_an_annotation_fact_naming_no_source_keeps_its_name(tmp_path: Path) -> None:
    with _before(tmp_path, ("blizzard",)) as (runner, engine, tenant, _project):
        _insert(engine, "forge_annotation_facts", [{"tenant_id": tenant, "source_name": "gone", "annotating": False}])

        runner.upgrade(_IDENTITY)

        assert {r[0] for r in _query(engine, "SELECT work_source_id FROM forge_annotation_facts")} == {
            _ids(engine)[(tenant, "blizzard")],
            "gone",
        }


def test_the_change_keys_of_the_two_kinds_hold_ids_tenant_by_tenant_and_the_diffs_stay(tmp_path: Path) -> None:
    with _before(tmp_path, ("blizzard", "Odd Name")) as (runner, engine, tenant, _project):
        runner.upgrade(_IDENTITY)

        ids = _ids(engine)
        keys = _query(engine, "SELECT tenant_id, record_kind, record_key, diff FROM config_changes")
        kinds = ("work_source", "project_source_link")
        expected = {(tenant, kind, ids[(tenant, slug)]) for slug in ("blizzard", "odd-name") for kind in kinds} | {
            (_SECOND, kind, ids[(_SECOND, "blizzard")]) for kind in kinds
        }
        assert {(t, k, key) for t, k, key, _ in keys if k in kinds} == expected
        assert {(k, key) for _, k, key, _ in keys if k in ("secret", "repository")} == {
            ("secret", "tok"),
            ("repository", "blizzard"),
        }
        assert {diff for _, k, _, diff in keys if k in kinds} == {'[{"field": "locator"}]'}
        assert _query(engine, "SELECT count(*) FROM config_changes") == [(len(keys),)]


def test_the_store_is_keyed_by_the_id_with_the_slug_unique_in_its_tenant(tmp_path: Path) -> None:
    with _before(tmp_path, ("blizzard",)) as (runner, engine, tenant, _project):
        runner.upgrade(_IDENTITY)

        inspector = sa.inspect(engine)
        assert inspector.get_pk_constraint("work_sources")["constrained_columns"] == ["work_source_id"]
        assert {u["name"] for u in inspector.get_unique_constraints("work_sources")} >= {"uq_work_sources_tenant_slug"}
        assert inspector.get_pk_constraint("project_source_links")["constrained_columns"] == [
            "project_id",
            "work_source_id",
        ]
        assert inspector.has_table("work_source_former_slugs")
        with pytest.raises(IntegrityError):
            _insert(
                engine,
                "work_sources",
                [{**_source(tenant, "again", 9), "slug": "blizzard", "work_source_id": "wsrc_x"}],
            )


def test_a_name_no_source_holds_refuses_the_upgrade_and_leaves_the_store(tmp_path: Path) -> None:
    with _before(tmp_path, ("blizzard",)) as (runner, engine, tenant, project):
        _insert(
            engine,
            "project_source_link_facts",
            [{"tenant_id": tenant, "project_id": project, "source_name": "ghost", "linked": True}],
        )

        with pytest.raises(RuntimeError, match="ghost") as refused:
            runner.upgrade(_IDENTITY)

        assert tenant in str(refused.value)
        assert runner.current_revision() == _BEFORE
        assert "source_name" in {c["name"] for c in sa.inspect(engine).get_columns("project_source_links")}
        assert {r[0] for r in _query(engine, "SELECT source FROM chunk_work_refs")} == {"blizzard", "hub", _STRANDED}


@pytest.mark.parametrize("steps", range(1, 6))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    names = ("blizzard", "Odd Name")
    with _before(tmp_path / "stopped", names) as (runner, engine, _tenant, _project):
        with engine.begin() as conn:
            for step in _revision_module().UPGRADE_STEPS[:steps]:
                _run_step(conn, step)

        runner.upgrade(_IDENTITY)

        assert runner.current_revision() == _IDENTITY
        with _before(tmp_path / "reference", names) as (reference_runner, reference, _, _):
            reference_runner.upgrade(_IDENTITY)
            assert _shape(engine) == _shape(reference)


def test_the_fills_page_in_bounded_batches_and_miss_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_revision_module(), "_BATCH_ROWS", 2)
    names = tuple(f"source-{n}" for n in range(7))
    with _before(tmp_path, names) as (runner, engine, tenant, _project):
        runner.upgrade(_IDENTITY)

        ids = _ids(engine)
        held = {ids[(tenant, name)] for name in names}
        assert {r[0] for r in _query(engine, "SELECT work_source_id FROM project_source_links")} == held
        for table in _WORK_REFS:
            assert {r[0] for r in _query(engine, f"SELECT source FROM {table}")} == {*held, "hub", _STRANDED}
        assert _query(engine, "SELECT count(*) FROM config_changes WHERE record_key LIKE 'wsrc\\_%' ESCAPE '\\'") == [
            (14 + 2,)
        ]


def test_the_downgrade_restores_the_name_keys_and_the_store_exactly_for_names_that_are_slugs(tmp_path: Path) -> None:
    with _before(tmp_path, ("blizzard", "docs")) as (runner, engine, _tenant, _project):
        shape = _shape(engine)

        runner.upgrade(_IDENTITY)
        runner.downgrade(_BEFORE)

        assert runner.current_revision() == _BEFORE
        assert _shape(engine) == shape


def test_the_downgrade_names_a_source_by_its_slug(tmp_path: Path) -> None:
    with _before(tmp_path, ("Odd Name",)) as (runner, engine, tenant, _project):
        runner.upgrade(_IDENTITY)
        runner.downgrade(_BEFORE)

        assert _query(engine, "SELECT name FROM work_sources WHERE tenant_id = :t", t=tenant) == [("odd-name",)]
        assert {r[0] for r in _query(engine, "SELECT source_name FROM project_source_links")} == {"odd-name"}
        assert {r[0] for r in _query(engine, "SELECT source FROM chunk_work_refs")} == {"odd-name", "hub", _STRANDED}
        assert {
            r[0] for r in _query(engine, "SELECT record_key FROM config_changes WHERE record_kind = 'work_source'")
        } == {"odd-name", "blizzard"}
