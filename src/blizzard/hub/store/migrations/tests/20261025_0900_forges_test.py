"""The forges revision carries every forge address a repository or work source holds into one forge per canonical
origin of its tenant, and points each record at it: a kept port stays, an empty ``api_base`` lands on ``github``, a forge
only retired records name is created retired, ids and slugs are the same on every run, and a run stopped after any step
finishes as a single run does. It refuses, before writing anything, the four cases the pre-flight check names — the same
records — and carries the store over once one record of each is edited. The downgrade returns the previous revision's
schema with the addresses canonical. Its frozen copies of the origin rule, the web derivation and the slug rule answer
as the live ones do.

Seeded from the store's reflected shape at the parent revision rather than ``src/blizzard/hub/store/schema.py``."""

from __future__ import annotations

import json
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

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.config.forge_check import ForgeCheck
from blizzard.hub.domain.config.forges import OriginRefused, canonical_origin
from blizzard.hub.domain.config.repositories import CommitOrigin, resolve_chunk_repositories
from blizzard.hub.domain.kernel.slugs import derive_slug
from blizzard.hub.forge.web import web_origin
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store.forge_check_reads import read_forge_addresses
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from blizzard.hub.tenancy import TenantId
from blizzard.tools.invariants import hub_invariants
from tests.migration_support_project_columns_migration import _NOW, _insert, _query, _run_step
from tests.migration_support_store_migrations import _schema_snapshot
from tests.repo_files import repo_root
from tests.support import scoped_connections

pytestmark = [pytest.mark.component]

_PARENT = "20261024_0900_hub_legacy_run"
_FORGES = "20261025_0900_forges"

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SECOND = "ten_second"
_FORGE_ID = re.compile(r"^frg_[0-9A-HJKMNP-TV-Z]{26}$")
_FORGE_INVARIANTS = frozenset(
    {"hub:forge-reference-standing", "hub:forge-references-in-tenant", "hub:slug-not-current-and-former"}
)

# Each repository as seeded: slug, forge_api_url, retired.
_REPOSITORIES: tuple[tuple[str, str, bool], ...] = (
    ("app", "https://api.github.com/", False),
    ("local", "http://127.0.0.1:8123/", False),
    ("ghe-app", "https://GHE.acme.internal/api/v3", False),
    ("old", "https://retired.example", True),
)
# Each GitHub work source as seeded: slug, api_base, web_base, retired.
_SOURCES: tuple[tuple[str, str | None, str | None, bool], ...] = (
    ("empty", "", None, False),
    ("null", None, None, False),
    ("ghe-issues", "https://ghe.acme.internal/api/v3/", "https://GHE.acme.internal/", False),
    ("ghe-other", "https://ghe.acme.internal/api/v3", None, False),
)


def _revision_module() -> ModuleType:
    revision = ScriptDirectory(str(MIGRATIONS_DIR)).get_revision(_FORGES)
    assert revision is not None
    return revision.module


def _repository(tenant: str, slug: str, url: str, n: int, *, owner: str = "acme", repo: str = "") -> dict[str, object]:
    return {
        "tenant_id": tenant,
        "repository_id": f"repo_{tenant}_{slug}",
        "slug": slug,
        "name": slug,
        "forge_api_url": url,
        "owner": owner,
        "repo": repo or slug,
        "base_branch": "main",
        "secret_name": "tok",
        "revision": 1,
        "created_at": _T0 + timedelta(minutes=n),
        "created_by": "op",
    }


def _source(
    tenant: str, slug: str, api_base: str | None, web_base: str | None, n: int, *, provider: str = "github"
) -> dict[str, object]:
    return {
        "tenant_id": tenant,
        "work_source_id": f"wsrc_{tenant}_{slug}",
        "slug": slug,
        "name": slug,
        "provider": provider,
        "locator": f"acme/{slug}",
        "api_base": api_base,
        "web_base": web_base,
        "annotate": True,
        "secret_name": "tok",
        "revision": 1,
        "created_at": _T0 + timedelta(minutes=10 + n),
        "created_by": "op",
    }


@contextmanager
def _before(tmp_path: Path) -> Iterator[tuple[MigrationRunner, sa.Engine, str]]:
    """A store at the parent revision: its own tenant holds :data:`_REPOSITORIES` and :data:`_SOURCES`, retired ones
    with a retiring fact newest and an earlier enabling one; a second tenant holds one repository on GitHub. The
    runner, the engine, and the store's own tenant id."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    runner = Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner
    runner.upgrade(_PARENT)
    engine = create_engine_from_url(db_url)
    try:
        _insert(engine, "tenants", [{"tenant_id": _SECOND, "name": "second", "created_by": "migration"}])
        (tenant,) = _query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id != :s", s=_SECOND)[0]
        _insert(engine, "secrets", [{"tenant_id": tenant, "name": "tok"}, {"tenant_id": _SECOND, "name": "tok"}])
        _insert(engine, "repositories", [_repository(tenant, s, url, n) for n, (s, url, _) in enumerate(_REPOSITORIES)])
        _insert(engine, "repositories", [_repository(_SECOND, "app", "https://api.github.com", 0)])
        _insert(
            engine,
            "work_sources",
            [_source(tenant, s, api, web, n) for n, (s, api, web, _) in enumerate(_SOURCES)],
        )
        for slug, *_, retired in _REPOSITORIES:
            if retired:
                facts = [
                    {"tenant_id": tenant, "repository_id": f"repo_{tenant}_{slug}", "retired": r} for r in (False, True)
                ]
                _insert(engine, "repository_lifecycle_facts", facts)
        yield runner, engine, tenant
    finally:
        engine.dispose()


def _forges(engine: sa.Engine, tenant: str) -> dict[str, tuple[Any, ...]]:
    """Each forge of ``tenant`` by slug: its name, api_base, web_base, created_at, kind, revision, created_by."""
    names = ("slug", "name", "api_base", "web_base", "created_at", "kind", "revision", "created_by", "tenant_id")
    types: dict[str, Any] = {"created_at": UtcDateTime(), "revision": sa.Integer()}
    forges = sa.table("forges", *(sa.column(c, types.get(c, sa.String())) for c in names))
    with engine.connect() as conn:
        rows = conn.execute(sa.select(*(forges.c[c] for c in names[:-1])).where(forges.c.tenant_id == tenant)).all()
    return {slug: tuple(rest) for slug, *rest in rows}


def _forge_ids(engine: sa.Engine) -> dict[tuple[str, str], str]:
    return {(t, slug): id_ for id_, t, slug in _query(engine, "SELECT forge_id, tenant_id, slug FROM forges")}


def _rows(engine: sa.Engine, table: str) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(row._mapping) for row in conn.execute(sa.select(sa.text("*")).select_from(sa.table(table)))]


def _labelled(value: object, labels: dict[str, str]) -> object:
    if not isinstance(value, str):
        return value
    for minted, label in labels.items():
        value = value.replace(minted, label)
    return value


def _shape(engine: sa.Engine) -> tuple[object, ...]:
    """The store's schema and every row, a change row's id aside, with every minted tenant and forge id read as a
    label."""
    (tenant,) = _query(engine, "SELECT tenant_id FROM tenants WHERE tenant_id != :s", s=_SECOND)[0]
    labels: dict[str, str] = {}
    if sa.inspect(engine).has_table("forges"):
        labels = {id_: f"forge-{t}-{slug}" for (t, slug), id_ in _forge_ids(engine).items()}
    (project,) = _query(engine, "SELECT project_id FROM projects WHERE tenant_id = :t", t=tenant)[0]
    labels = {
        **{k: str(_labelled(v, {tenant: "tenant"})) for k, v in labels.items()},
        tenant: "tenant",
        project: "project",
    }
    rows = {
        table: sorted(
            repr(sorted((c, _labelled(v, labels)) for c, v in row.items() if c != "id")) for row in _rows(engine, table)
        )
        for table in sorted(sa.inspect(engine).get_table_names())
    }
    return _schema_snapshot(engine, exemptions={}, name_exemptions={}), rows


def _judged_by_the_check(engine: sa.Engine) -> list[tuple[str, str, tuple[str, ...]]]:
    with engine.connect() as conn:
        check = ForgeCheck.of(read_forge_addresses(conn))
    return [(t.tenant_id, r.case.value, r.slugs) for t in check.tenants for r in t.refusals]


def _judged_by_the_revision(engine: sa.Engine) -> list[tuple[str, str, tuple[str, ...]]]:
    with engine.connect() as conn:
        refused = _revision_module()._refusals(conn)
    return [(tenant.tenant_id, r.case, r.slugs) for tenant, refusals in refused for r in refusals]


def test_every_origin_becomes_one_forge_of_its_tenant(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        runner.upgrade(_FORGES)

        ghe = "https://ghe.acme.internal/api/v3"
        assert _forges(engine, tenant) == {
            "github": ("GitHub", "https://api.github.com", None, _T0, "github", 1, "migration"),
            "127-0-0-1-8123": (
                "127.0.0.1:8123",
                "http://127.0.0.1:8123",
                None,
                _T0 + timedelta(minutes=1),
                "github",
                1,
                "migration",
            ),
            "ghe-acme-internal": (
                "ghe.acme.internal",
                ghe,
                "https://ghe.acme.internal",
                _T0 + timedelta(minutes=2),
                "github",
                1,
                "migration",
            ),
            "retired-example": (
                "retired.example",
                "https://retired.example",
                None,
                _T0 + timedelta(minutes=3),
                "github",
                1,
                "migration",
            ),
        }
        assert set(_forges(engine, _SECOND)) == {"github"}
        ids = _forge_ids(engine)
        assert all(_FORGE_ID.match(id_) for id_ in ids.values())
        assert len(set(ids.values())) == len(ids)


def test_every_record_names_its_forge_and_holds_its_forges_addresses(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        runner.upgrade(_FORGES)

        ids = _forge_ids(engine)
        github, local = ids[(tenant, "github")], ids[(tenant, "127-0-0-1-8123")]
        ghe, retired = ids[(tenant, "ghe-acme-internal")], ids[(tenant, "retired-example")]
        repositories = _query(
            engine, "SELECT slug, forge_id, forge_api_url FROM repositories WHERE tenant_id = :t", t=tenant
        )
        assert sorted(repositories) == [
            ("app", github, "https://api.github.com"),
            ("ghe-app", ghe, "https://ghe.acme.internal/api/v3"),
            ("local", local, "http://127.0.0.1:8123"),
            ("old", retired, "https://retired.example"),
        ]
        sources = _query(
            engine, "SELECT slug, forge_id, api_base, web_base FROM work_sources WHERE tenant_id = :t", t=tenant
        )
        assert sorted(sources) == [
            ("empty", github, "https://api.github.com", None),
            ("ghe-issues", ghe, "https://ghe.acme.internal/api/v3", "https://ghe.acme.internal"),
            ("ghe-other", ghe, "https://ghe.acme.internal/api/v3", "https://ghe.acme.internal"),
            ("null", github, "https://api.github.com", None),
        ]
        assert _query(engine, "SELECT forge_id FROM repositories WHERE tenant_id = :t", t=_SECOND) == [
            (ids[(_SECOND, "github")],)
        ]
        assert _query(engine, "SELECT DISTINCT revision FROM repositories") == [(1,)]


def test_a_forge_only_retired_records_name_is_created_retired_with_its_change_rows(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        runner.upgrade(_FORGES)

        ids = _forge_ids(engine)
        assert _query(engine, "SELECT forge_id, retired, set_by FROM forge_lifecycle_facts") == [
            (ids[(tenant, "retired-example")], True, "migration")
        ]
        changes = _query(
            engine,
            "SELECT tenant_id, record_key, actor, door, op, revision, diff, project_id FROM config_changes "
            "WHERE record_kind = 'forge'",
        )
        assert {(t, key) for t, key, *_ in changes} == {(t, id_) for (t, _), id_ in ids.items()}
        assert len(changes) == len(ids)
        assert {(actor, door, op, rev, project) for _, _, actor, door, op, rev, _, project in changes} == {
            ("migration", "migration", "create", 1, None)
        }
        diffs = {key: json.loads(diff) for _, key, *_, diff, _ in changes}
        assert diffs[ids[(tenant, "ghe-acme-internal")]] == [
            {"field": "slug", "old": None, "new": "ghe-acme-internal"},
            {"field": "name", "old": None, "new": "ghe.acme.internal"},
            {"field": "kind", "old": None, "new": "github"},
            {"field": "api_base", "old": None, "new": "https://ghe.acme.internal/api/v3"},
            {"field": "web_base", "old": None, "new": "https://ghe.acme.internal"},
        ]


def test_a_carried_over_store_holds_every_forge_invariant(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _tenant):
        # The invariants read every table the live schema declares, so the store walks on to head.
        runner.upgrade("head")

        assert [v for v in hub_invariants(engine).run() if v.invariant in _FORGE_INVARIANTS] == []


def test_a_chunks_commits_resolve_to_the_same_repositories_after_carry_over(tmp_path: Path) -> None:
    # Each origin a delivered commit names, and the repository the host-of-its-own-address rule resolved it to below
    # the revision.
    commits = {
        "https://github.com/acme/app.git": "app",
        "http://127.0.0.1:8123/acme/local.git": "local",
        "https://ghe.acme.internal/acme/ghe-app.git": "ghe-app",
    }
    with _before(tmp_path) as (runner, engine, tenant):
        # The record stores read every column the live schema declares, so the store walks on to head.
        runner.upgrade("head")
        connections = scoped_connections(engine, TenantId(tenant))
        records = RepositoryRecordStore(connections).list_all(include_retired=True)
        forges = ForgeRecordStore(connections).list_all(include_retired=True)

        resolution = resolve_chunk_repositories(
            [CommitOrigin.of(origin, slug) for origin, slug in commits.items()],
            records,
            forges,
            minted_at=_T0 + timedelta(days=1),
            linked=None,
        )

        assert [target.slug for target in resolution.targets] == list(commits.values())


def test_the_records_whose_web_links_move_are_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with _before(tmp_path) as (runner, _engine, _tenant):
        with caplog.at_level(logging.WARNING, logger="alembic.runtime.migration"):
            runner.upgrade(_FORGES)

        assert "work source ghe-other" in caplog.text
        assert "work source ghe-issues" not in caplog.text
        assert "repository ghe-app" not in caplog.text


def test_a_slug_another_forge_of_the_tenant_holds_is_suffixed(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        _insert(
            engine,
            "repositories",
            [
                _repository(tenant, "dash", "https://a-b.example", 20),
                _repository(tenant, "dot", "https://a.b.example", 21),
            ],
        )

        runner.upgrade(_FORGES)

        forges = _forges(engine, tenant)
        assert forges["a-b-example"][1] == "https://a-b.example"
        assert forges["a-b-example-2"][1] == "https://a.b.example"


def test_ids_and_slugs_are_the_same_on_every_run(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, _):
        runner.upgrade(_FORGES)
        first = _query(engine, "SELECT forge_id, tenant_id, slug, name FROM forges ORDER BY forge_id")
        runner.downgrade(_PARENT)
        runner.upgrade(_FORGES)

        assert _query(engine, "SELECT forge_id, tenant_id, slug, name FROM forges ORDER BY forge_id") == first


@pytest.mark.parametrize("steps", range(1, 6))
def test_a_run_stopped_after_any_step_finishes_as_a_single_run_does(tmp_path: Path, steps: int) -> None:
    (tmp_path / "stopped").mkdir()
    (tmp_path / "reference").mkdir()
    with _before(tmp_path / "stopped") as (runner, engine, _):
        with engine.begin() as conn:
            for step in _revision_module().UPGRADE_STEPS[:steps]:
                _run_step(conn, step)

        runner.upgrade(_FORGES)

        assert runner.current_revision() == _FORGES
        with _before(tmp_path / "reference") as (reference_runner, reference, _):
            reference_runner.upgrade(_FORGES)
            assert _shape(engine) == _shape(reference)


def test_the_updates_page_in_bounded_batches_and_miss_no_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_revision_module(), "_BATCH_ROWS", 2)
    with _before(tmp_path) as (runner, engine, tenant):
        _insert(
            engine,
            "repositories",
            [_repository(tenant, f"many-{n}", "https://api.github.com", 30 + n) for n in range(7)],
        )

        runner.upgrade(_FORGES)

        assert _query(engine, "SELECT count(*) FROM repositories WHERE forge_id IS NULL") == [(0,)]
        assert _query(engine, "SELECT count(*) FROM work_sources WHERE forge_id IS NULL") == [(0,)]


def test_the_downgrade_returns_the_parent_schema_with_the_addresses_canonical(tmp_path: Path) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        schema = _schema_snapshot(engine, exemptions={}, name_exemptions={})
        changes = _query(engine, "SELECT count(*) FROM config_changes")

        runner.upgrade(_FORGES)
        runner.downgrade(_PARENT)

        assert runner.current_revision() == _PARENT
        assert _schema_snapshot(engine, exemptions={}, name_exemptions={}) == schema
        assert _query(engine, "SELECT count(*) FROM config_changes") == changes
        assert sorted(
            _query(engine, "SELECT slug, forge_api_url FROM repositories WHERE tenant_id = :t", t=tenant)
        ) == [
            ("app", "https://api.github.com"),
            ("ghe-app", "https://ghe.acme.internal/api/v3"),
            ("local", "http://127.0.0.1:8123"),
            ("old", "https://retired.example"),
        ]
        assert ("empty", "https://api.github.com") in _query(
            engine, "SELECT slug, api_base FROM work_sources WHERE tenant_id = :t", t=tenant
        )
        runner.upgrade(_FORGES)
        assert runner.current_revision() == _FORGES


# Each refusal case: the rows that plant it, and the edit that settles it.
_Plant = tuple[str, list[tuple[str, dict[str, object]]], tuple[str, str, dict[str, object]]]


def _plants(tenant: str) -> list[_Plant]:
    return [
        (
            "not-an-origin",
            [("work_sources", _source(tenant, "noscheme", "ghe.acme", None, 5))],
            ("work_sources", f"wsrc_{tenant}_noscheme", {"api_base": "https://ghe.acme"}),
        ),
        (
            "needs-cleaning",
            [("repositories", _repository(tenant, "user", "https://user@forge.example", 5))],
            ("repositories", f"repo_{tenant}_user", {"forge_api_url": "https://forge.example"}),
        ),
        (
            "coordinate-collision",
            [
                ("repositories", _repository(tenant, "twin-a", "https://forge.example", 5, repo="twin")),
                ("repositories", _repository(tenant, "twin-b", "https://FORGE.example/", 6, repo="twin")),
            ],
            ("repositories", f"repo_{tenant}_twin-b", {"repo": "twin-renamed"}),
        ),
        (
            "web-base-disagreement",
            [
                ("work_sources", _source(tenant, "web-a", "https://web.example/api/v3", "https://web.example", 5)),
                ("work_sources", _source(tenant, "web-b", "https://web.example/api/v3", "https://www.web.example", 6)),
            ],
            ("work_sources", f"wsrc_{tenant}_web-b", {"web_base": "https://web.example/"}),
        ),
    ]


def _keyed(table: str) -> str:
    return "repository_id" if table == "repositories" else "work_source_id"


@pytest.mark.parametrize("case", ["not-an-origin", "needs-cleaning", "coordinate-collision", "web-base-disagreement"])
def test_each_refusal_names_the_records_the_check_names_and_an_edit_settles_it(tmp_path: Path, case: str) -> None:
    with _before(tmp_path) as (runner, engine, tenant):
        _, planted, (table, key, edit) = next(p for p in _plants(tenant) if p[0] == case)
        for into, row in planted:
            _insert(engine, into, [row])
        slugs = tuple(str(row["slug"]) for _, row in planted)

        assert _judged_by_the_revision(engine) == _judged_by_the_check(engine) == [(tenant, case, slugs)]
        with pytest.raises(RuntimeError, match=case) as refused:
            runner.upgrade(_FORGES)
        assert all(slug in str(refused.value) for slug in slugs)
        assert runner.current_revision() == _PARENT
        assert not sa.inspect(engine).has_table("forges")

        with engine.begin() as conn:
            target = sa.table(table, *(sa.column(c) for c in (_keyed(table), *edit)))
            conn.execute(sa.update(target).where(target.c[_keyed(table)] == key).values(edit))
        assert _judged_by_the_revision(engine) == _judged_by_the_check(engine) == []
        runner.upgrade(_FORGES)
        assert runner.current_revision() == _FORGES


def test_every_refusal_of_a_store_is_named_by_both(tmp_path: Path) -> None:
    with _before(tmp_path) as (_runner, engine, tenant):
        for _, planted, _ in _plants(tenant):
            for into, row in planted:
                _insert(engine, into, [row])

        judged = _judged_by_the_revision(engine)
        assert judged == _judged_by_the_check(engine)
        assert {case for _, case, _ in judged} == {
            "not-an-origin",
            "needs-cleaning",
            "coordinate-collision",
            "web-base-disagreement",
        }


def _cases() -> list[dict[str, str]]:
    return json.loads((repo_root() / "tests" / "fixtures" / "canonical_origin_cases.json").read_text())


@pytest.mark.parametrize("case", _cases(), ids=lambda case: case["case"])
def test_the_frozen_origin_rule_answers_every_case_as_the_live_one(case: dict[str, str]) -> None:
    frozen = _revision_module()
    try:
        live: tuple[str, str] = ("origin", canonical_origin(case["input"]))
    except OriginRefused as refused:
        live = ("refused", refused.kind.value)
    try:
        answered: tuple[str, str] = ("origin", frozen._canonical_origin(case["input"]))
    except frozen._Refused as refused:
        answered = ("refused", refused.case)
    assert answered == live
    assert answered == (("origin", case["origin"]) if "origin" in case else ("refused", case["refused"]))


@pytest.mark.parametrize(
    "api_base",
    ["https://api.github.com", "https://ghe.acme.internal/api/v3", "http://127.0.0.1:8123", "https://forge.example/"],
)
def test_the_frozen_web_derivation_answers_as_the_live_one(api_base: str) -> None:
    assert _revision_module()._derived_web_origin(api_base) == web_origin(api_base)


@pytest.mark.parametrize("raw", ["ghe.acme.internal", "127.0.0.1:8123", "[::1]:8443", "...", "A" * 80])
def test_the_frozen_slug_rule_answers_as_the_live_one(raw: str) -> None:
    assert _revision_module()._derived(raw) == derive_slug(raw, fallback="forge")
