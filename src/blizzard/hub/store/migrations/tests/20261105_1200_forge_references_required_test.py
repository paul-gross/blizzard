"""The forge-references-required revision: a transition-era store crosses over keyed on its forges, the address
columns gone; a record naming no forge of its tenant is refused before any write, named; a re-run changes nothing; and
the downgrade restores the addresses from the forges and the address-keyed constraints, refusing first one locator held
on two forges."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.store import schema as s
from tests.support.migrations.project_columns_migration import insert, query, run_step
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import migrate_to

pytestmark = pytest.mark.component

_PARENT = "20261105_0900_routine_slug"
_REVISION = "20261105_1200_forge_references_required"
_MODULE = importlib.import_module(f"blizzard.hub.store.migrations.versions.{_REVISION}")
_TENANT = "ten_a"
_OTHER = "ten_b"
_GITHUB = ("frg_gh", "https://api.github.com", None)
_GHE = ("frg_ghe", "https://ghe.example.com/api/v3", "https://ghe.example.com")


def _seed_forges(engine: sa.Engine) -> None:
    insert(engine, "tenants", [{"tenant_id": t, "name": t.removeprefix("ten_")} for t in (_TENANT, _OTHER)])
    insert(
        engine,
        "forges",
        [
            dict[str, object](forge_id=f, tenant_id=_TENANT, slug=f, name=f, kind="github", api_base=a, web_base=w)
            for f, a, w in (_GITHUB, _GHE)
        ]
        + [
            {
                "forge_id": "frg_other",
                "tenant_id": _OTHER,
                "slug": "github",
                "name": "GitHub",
                "kind": "github",
                "api_base": _GITHUB[1],
                "web_base": None,
            }
        ],
    )
    insert(engine, "forge_lifecycle_facts", [{"forge_id": _GHE[0], "retired": True, "tenant_id": _TENANT}])


def _repository(slug: str, forge: tuple[str, str, str | None] | None, *, tenant: str = _TENANT) -> dict[str, object]:
    forge_id, api_base = (None, "https://api.github.com") if forge is None else forge[:2]
    return {
        "repository_id": f"repo_{slug}",
        "slug": slug,
        "forge_id": forge_id,
        "forge_api_url": api_base,
        "owner": "acme",
        "repo": slug,
        "tenant_id": tenant,
    }


def _source(
    slug: str, forge: tuple[str, str, str | None] | None, *, locator: str | None = None, tenant: str = _TENANT
) -> dict[str, object]:
    forge_id, api_base, web_base = (None, None, None) if forge is None else forge
    return {
        "work_source_id": f"wsrc_{slug}",
        "slug": slug,
        "provider": "github",
        "locator": locator or f"acme/{slug}",
        "forge_id": forge_id,
        "api_base": api_base,
        "web_base": web_base,
        "annotate": False,
        "tenant_id": tenant,
    }


def _seed_transition_store(engine: sa.Engine) -> None:
    _seed_forges(engine)
    insert(engine, "repositories", [_repository("core", _GITHUB), _repository("legacy", _GHE)])
    insert(
        engine, "repository_lifecycle_facts", [{"repository_id": "repo_legacy", "retired": True, "tenant_id": _TENANT}]
    )
    insert(engine, "work_sources", [_source("issues", _GITHUB), _source("old-issues", _GHE)])
    insert(
        engine,
        "work_source_lifecycle_facts",
        [{"work_source_id": "wsrc_old-issues", "retired": True, "tenant_id": _TENANT}],
    )


def _columns(engine: sa.Engine, table: str) -> dict[str, bool]:
    return {str(c["name"]): bool(c["nullable"]) for c in sa.inspect(engine).get_columns(table)}


def _uniques(engine: sa.Engine, table: str) -> dict[str, tuple[str, ...]]:
    return {str(u["name"]): tuple(u["column_names"]) for u in sa.inspect(engine).get_unique_constraints(table)}


def _foreign_keys(engine: sa.Engine, table: str) -> dict[str, tuple[str, tuple[str, ...]]]:
    return {
        str(f["name"]): (str(f["referred_table"]), tuple(f["constrained_columns"]))
        for f in sa.inspect(engine).get_foreign_keys(table)
    }


def _version(engine: sa.Engine) -> str:
    return str(query(engine, "SELECT version_num FROM alembic_version")[0][0])


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_a_transition_era_store_crosses_over_keyed_on_its_forges(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed_transition_store(engine)

    runner.upgrade(_REVISION)

    assert query(engine, "SELECT slug, forge_id FROM repositories ORDER BY slug") == [
        ("core", _GITHUB[0]),
        ("legacy", _GHE[0]),
    ]
    assert query(engine, "SELECT slug, forge_id FROM work_sources ORDER BY slug") == [
        ("issues", _GITHUB[0]),
        ("old-issues", _GHE[0]),
    ]
    repositories, sources = _columns(engine, "repositories"), _columns(engine, "work_sources")
    assert "forge_api_url" not in repositories and repositories["forge_id"] is False
    assert not {"api_base", "web_base"} & sources.keys() and sources["forge_id"] is False
    assert _foreign_keys(engine, "repositories")["fk_repositories_forges"] == ("forges", ("forge_id",))
    assert _foreign_keys(engine, "work_sources")["fk_work_sources_forges"] == ("forges", ("forge_id",))
    assert _uniques(engine, "repositories")["uq_repositories_tenant_coordinate"] == (
        "tenant_id",
        "forge_id",
        "owner",
        "repo",
    )
    assert _uniques(engine, "work_sources")["uq_work_sources_tenant_provider_locator"] == (
        "tenant_id",
        "provider",
        "forge_id",
        "locator",
    )
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        tables = ("repositories", "work_sources")
        assert {t: _snapshot(engine)[t] for t in tables} == {t: _snapshot(declared)[t] for t in tables}
    finally:
        declared.dispose()


def test_one_locator_on_two_forges_is_admitted_once_keyed(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed_transition_store(engine)
    runner.upgrade(_REVISION)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO work_sources (work_source_id, slug, name, provider, locator, forge_id, annotate,"
                " revision, created_at, created_by, tenant_id) VALUES ('wsrc_twin', 'twin', 'twin', 'github',"
                " 'acme/issues', :forge, 0, 1, '2026-11-05', 'op', :tenant)"
            ),
            {"forge": _GHE[0], "tenant": _TENANT},
        )
    assert query(engine, "SELECT count(*) FROM work_sources WHERE locator = 'acme/issues'") == [(2,)]


@pytest.mark.parametrize(
    ("table", "row", "reason"),
    [
        pytest.param("repositories", _repository("stray", None), "repository stray names no forge", id="repo-null"),
        pytest.param("work_sources", _source("stray", None), "work source stray names no forge", id="source-null"),
        pytest.param(
            "repositories",
            _repository("stray", ("frg_gone", "https://gone.example.com", None)),
            "repository stray names forge frg_gone, which does not exist",
            id="repo-missing",
        ),
        pytest.param(
            "work_sources",
            _source("stray", ("frg_other", _GITHUB[1], None)),
            "work source stray names forge frg_other of tenant ten_b",
            id="source-other-tenant",
        ),
    ],
)
def test_a_record_naming_no_forge_of_its_tenant_is_refused_before_any_write(
    tmp_path: Path, table: str, row: dict[str, object], reason: str
) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed_transition_store(engine)
    insert(engine, table, [row])
    before = _snapshot(engine)

    with pytest.raises(RuntimeError) as refused:
        runner.upgrade(_REVISION)

    assert f"tenant a ({_TENANT}): {reason}" in str(refused.value)
    assert _version(engine) == _PARENT
    assert _snapshot(engine) == before


def test_a_rerun_over_a_crossed_store_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed_transition_store(engine)
    runner.upgrade(_REVISION)
    crossed = _snapshot(engine)
    with engine.begin() as conn:
        for step in _MODULE.UPGRADE_STEPS:
            run_step(conn, step)
    assert _snapshot(engine) == crossed


def test_the_downgrade_restores_the_addresses_from_the_forges(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    parent = _snapshot(engine)
    _seed_transition_store(engine)
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == parent
    assert query(engine, "SELECT slug, forge_id, forge_api_url FROM repositories ORDER BY slug") == [
        ("core", _GITHUB[0], _GITHUB[1]),
        ("legacy", _GHE[0], _GHE[1]),
    ]
    assert query(engine, "SELECT slug, forge_id, api_base, web_base FROM work_sources ORDER BY slug") == [
        ("issues", *_GITHUB),
        ("old-issues", *_GHE),
    ]


def test_the_downgrade_refuses_one_locator_on_two_forges_naming_the_pair(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed_transition_store(engine)
    runner.upgrade(_REVISION)
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO work_sources (work_source_id, slug, name, provider, locator, forge_id, annotate,"
                " revision, created_at, created_by, tenant_id) VALUES ('wsrc_twin', 'twin', 'twin', 'github',"
                " 'acme/issues', :forge, 0, 1, '2026-11-05', 'op', :tenant)"
            ),
            {"forge": _GHE[0], "tenant": _TENANT},
        )
    head = _snapshot(engine)

    with pytest.raises(RuntimeError) as refused:
        runner.downgrade(_PARENT)

    assert (
        f"tenant a ({_TENANT}): github acme/issues is held by work sources issues (forge {_GITHUB[0]}), "
        f"twin (forge {_GHE[0]})"
    ) in str(refused.value)
    assert _version(engine) == _REVISION
    assert _snapshot(engine) == head
