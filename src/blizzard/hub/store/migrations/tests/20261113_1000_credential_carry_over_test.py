"""The credential carry-over revision gives every repository and work source that names a secret a credential: one
shared by every use of a (forge, secret), distinct slugs for one secret on two forges, none for a work source without a
secret, a retired one for a retired secret, and the credential a door already created for an authority reused. It
re-runs idempotently, leaves ``secret_name`` alone, holds the credential invariants, and its downgrade removes only what
it created."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.tools.invariants import hub_invariants
from tests.support.migrations.project_columns_migration import insert, query
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261113_0900_host_urls"
_REVISION = "20261113_1000_credential_carry_over"
_AT = datetime(2026, 11, 1, tzinfo=UTC)
_ACTOR = "migration:credential-carry-over"
_CREDENTIAL_INVARIANTS = frozenset(
    {
        "hub:credential-references-in-tenant",
        "hub:credential-forge-matches",
        "hub:credential-secret-mirrored",
        "hub:credential-reference-standing",
        "hub:credential-authority-derived",
        "hub:slug-not-current-and-former",
    }
)


def _forge(tenant: str, forge_id: str, slug: str) -> dict[str, object]:
    return {
        "forge_id": forge_id,
        "slug": slug,
        "name": slug,
        "kind": "github",
        "api_base": f"https://{slug}.example/api",
        "web_base": None,
        "revision": 1,
        "created_at": _AT,
        "created_by": "op",
        "tenant_id": tenant,
    }


def _repository(tenant: str, slug: str, forge: str, secret: str, *, credential: str | None = None) -> dict[str, object]:
    return {
        "repository_id": f"repo_{slug}",
        "slug": slug,
        "name": slug,
        "owner": "acme",
        "repo": slug,
        "base_branch": "main",
        "secret_name": secret,
        "revision": 1,
        "created_at": _AT,
        "created_by": "op",
        "tenant_id": tenant,
        "forge_id": forge,
        "credential_id": credential,
    }


def _source(tenant: str, slug: str, forge: str, secret: str | None) -> dict[str, object]:
    return {
        "work_source_id": f"wsrc_{slug}",
        "slug": slug,
        "name": slug,
        "provider": "github",
        "locator": f"acme/{slug}",
        "annotate": True,
        "secret_name": secret,
        "revision": 1,
        "created_at": _AT,
        "created_by": "op",
        "tenant_id": tenant,
        "forge_id": forge,
    }


def _seed(engine: sa.Engine, tenant: str) -> None:
    """Forges ``fa`` and ``fb``; secrets ``tok`` (shared), ``other``, ``old`` (retired) and ``door`` (a credential a
    door created already holds it on ``fa``); and the referrers that name them."""
    insert(engine, "forges", [_forge(tenant, "frg_a", "fa"), _forge(tenant, "frg_b", "fb")])
    insert(engine, "secrets", [{"tenant_id": tenant, "name": n} for n in ("tok", "other", "old", "door")])
    insert(engine, "secret_lifecycle_facts", [{"tenant_id": tenant, "name": "old", "retired": True}])
    insert(
        engine,
        "credentials",
        [
            {
                "credential_id": "cred_door",
                "slug": "tok",  # takes the slug the migration would pick for ``tok``
                "name": "door",
                "forge_id": "frg_a",
                "binding": "github.token",
                "secret_name": "door",
                "settings": '{"variant": "classic"}',
                "authority": "secret/door",
                "revision": 3,
                "created_at": _AT,
                "created_by": "op",
                "tenant_id": tenant,
            }
        ],
    )
    insert(
        engine,
        "credential_former_slugs",
        [{"slug": "other", "credential_id": "cred_door", "tenant_id": tenant, "left_at": _AT}],
    )
    insert(
        engine,
        "repositories",
        [
            _repository(tenant, "shared-repo", "frg_a", "tok"),
            _repository(tenant, "other-forge", "frg_b", "tok"),
            _repository(tenant, "former", "frg_a", "other"),
            _repository(tenant, "retired-secret", "frg_a", "old"),
            _repository(tenant, "by-door", "frg_a", "door"),
        ],
    )
    insert(
        engine,
        "work_sources",
        [_source(tenant, "shared-source", "frg_a", "tok"), _source(tenant, "no-secret", "frg_a", None)],
    )


def _credentials(engine: sa.Engine) -> dict[tuple[str, str], tuple[str, str, int, str]]:
    """Each credential by (forge, secret): its id, slug, revision and creator."""
    rows = query(engine, "SELECT forge_id, secret_name, credential_id, slug, revision, created_by FROM credentials")
    return {(f, s): (i, slug, r, by) for f, s, i, slug, r, by in rows}


def _links(engine: sa.Engine) -> dict[str, str | None]:
    rows = query(
        engine,
        "SELECT repository_id, credential_id FROM repositories UNION SELECT work_source_id, credential_id FROM work_sources",
    )
    return dict(rows)


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def test_a_shared_secret_yields_one_credential_and_one_on_each_forge_distinct_in_slug(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))

    runner.upgrade(_REVISION)

    created = _credentials(engine)
    shared, elsewhere = created[("frg_a", "tok")], created[("frg_b", "tok")]
    links = _links(engine)
    assert links["repo_shared-repo"] == links["wsrc_shared-source"] == shared[0]
    assert links["repo_other-forge"] == elsewhere[0] != shared[0]
    # ``tok`` is the door credential's slug and ``other`` its former slug; one secret on two forges takes ``-2``.
    assert {shared[1], elsewhere[1]} == {"tok-2", "tok-3"}
    assert created[("frg_a", "other")][1] == "other-2"
    assert {c[3] for k, c in created.items() if k != ("frg_a", "door")} == {_ACTOR}
    assert query(engine, "SELECT count(*) FROM repositories WHERE secret_name IS NULL") == [(0,)]


def test_a_work_source_without_a_secret_keeps_none(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))

    runner.upgrade(_REVISION)

    assert _links(engine)["wsrc_no-secret"] is None


def test_a_retired_secret_yields_a_retired_credential_unless_an_enabled_referrer_names_it(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _seed(engine, tenant)
    insert(
        engine,
        "repository_lifecycle_facts",
        [{"tenant_id": tenant, "repository_id": "repo_retired-secret", "retired": True}],
    )

    runner.upgrade(_REVISION)

    old = _credentials(engine)[("frg_a", "old")][0]
    facts = query(
        engine, "SELECT retired, set_by FROM credential_lifecycle_facts WHERE credential_id = :c ORDER BY id", c=old
    )
    assert facts == [(True, _ACTOR)]


def test_an_enabled_referrer_enables_the_retired_credential_it_names(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))

    runner.upgrade(_REVISION)

    old, _, revision, _ = _credentials(engine)[("frg_a", "old")]
    facts = query(
        engine, "SELECT retired, set_by FROM credential_lifecycle_facts WHERE credential_id = :c ORDER BY id", c=old
    )
    assert facts == [(True, _ACTOR), (False, _ACTOR)]
    assert revision == 2
    ops = query(engine, "SELECT op, revision, diff FROM config_changes WHERE record_key = :c ORDER BY id", c=old)
    assert [(op, rev) for op, rev, _ in ops] == [("create", 1), ("enable", 2)]
    assert json.loads(ops[0][2])[-1] == {"field": "settings", "old": None, "new": {}}


def test_the_credential_a_door_created_is_linked_not_duplicated(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))

    runner.upgrade(_REVISION)

    created = _credentials(engine)
    assert created[("frg_a", "door")] == ("cred_door", "tok", 3, "op")
    assert _links(engine)["repo_by-door"] == "cred_door"
    assert query(engine, "SELECT count(*) FROM credentials WHERE secret_name = 'door'") == [(1,)]


def test_a_rerun_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    # Re-seeding at the parent is the case that matters, so seed there and run twice.
    runner.downgrade(_PARENT)
    _seed(engine, default_tenant(engine))
    runner.upgrade(_REVISION)
    once = (_credentials(engine), _links(engine), query(engine, "SELECT count(*) FROM config_changes"))
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert (_credentials(engine), _links(engine), query(engine, "SELECT count(*) FROM config_changes")) == once


def test_every_credential_invariant_holds_after_the_revision(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _seed(engine, tenant)
    # An enabled referrer on a retired secret is no store the invariants accept, so the case is a retired referrer.
    insert(
        engine,
        "repository_lifecycle_facts",
        [{"tenant_id": tenant, "repository_id": "repo_retired-secret", "retired": True}],
    )
    runner.upgrade("head")

    assert [v for v in hub_invariants(engine).run() if v.invariant in _CREDENTIAL_INVARIANTS] == []


def test_the_downgrade_removes_only_what_it_created(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))
    before = (_snapshot(engine), _links(engine))
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before[0]
    assert set(_credentials(engine)) == {("frg_a", "door")}
    assert _links(engine) == {**before[1], "repo_by-door": "cred_door"}
