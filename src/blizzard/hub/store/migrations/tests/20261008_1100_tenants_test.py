"""Tests proving the ``20261008_1100_tenants`` revision."""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationClockMissingError, MigrationRunner
from blizzard.foundation.store.utc import UtcDateTime
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations, migration_runner
from blizzard.hub.store import MIGRATIONS_DIR
from blizzard.hub.store.schema import GLOBAL_TABLES
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from tests.config_inputs import init_hub
from tests.migration_support_carried_config_round_trip import _AT, _BEFORE_TENANTS, _NOW
from tests.migration_support_carried_config_round_trip import _SEED as _CARRIED_SEED
from tests.migration_support_tenant_carry_over_migration import (
    _BEFORE,
    _SEED,
    _T0,
    _TENANTS,
    _foreign_key_violations,
    _keyed_tables,
    _row_counts,
    _schema,
    _seed_every_tenant_owned_table,
    _store,
    _tables,
    _tenant_rows,
    _tenants_of,
)
from tests.support import build_hub

pytestmark = [pytest.mark.component]

_RUNNER_ID = "run_carried"

_CARRIED_RUNNER = (
    "INSERT INTO runner_registrations (runner_id, name, workspace_id, registered_at, last_seen_at, added_at, added_by)"
    f" VALUES ('{_RUNNER_ID}', 'r-carried', 'ws-carried', {_AT}, {_AT}, {_AT}, 'operator')"
)


def _carried_store(root: Path) -> None:
    root.mkdir()
    db_url = f"sqlite:///{root / 'hub.db'}"
    Migrations(HubConfig(root=root, db_url=db_url), FixedClock(_NOW)).runner.upgrade(_BEFORE_TENANTS)
    engine = create_engine_from_url(db_url)
    try:
        with engine.begin() as conn:
            for statement in (*_CARRIED_SEED, _CARRIED_RUNNER):
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()


_ADDED = {"tenants", "tenant_deletions", "invitations", "invitation_facts", "hub_event_log"}


def _assert_the_first_tenant(engine: sa.Engine) -> None:
    [(tenant_id, name, created_at, created_by)] = _tenant_rows(engine)
    parsed = Id.parse(tenant_id)
    assert parsed is not None and parsed.has_prefix(IdPrefix.TENANT)
    assert parsed.minted_at is not None
    assert (name, created_by) == ("default", "migration")
    assert created_at is not None


def _project_change_rows(engine: sa.Engine) -> int:
    """The change rows the projects revision writes: the default project's and its links'."""
    kinds = "('project', 'project_source_link', 'project_repository_link')"
    with engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT count(*) FROM config_changes WHERE record_kind IN {kinds}")).scalar_one()


def _tenant_keys(engine: sa.Engine) -> dict[str, tuple[bool, str | None]]:
    """Every table outside the global list: whether its ``tenant_id`` admits null, and the name of the foreign key
    that points it at ``tenants``."""
    inspector = sa.inspect(engine)
    return {
        table: (
            next(c["nullable"] for c in inspector.get_columns(table) if c["name"] == "tenant_id"),
            next(
                (
                    fk["name"]
                    for fk in inspector.get_foreign_keys(table)
                    if (fk["constrained_columns"], fk["referred_table"]) == (["tenant_id"], "tenants")
                ),
                None,
            ),
        )
        for table in _tables(engine) - GLOBAL_TABLES
    }


def test_a_carried_runner_naming_no_projects_keeps_serving_default(tmp_path: Path) -> None:
    _carried_store(tmp_path / "hub")
    hub = build_hub(tmp_path / "hub")
    assert hub.app is not None
    (default,) = hub.client.get("/api/projects").json()["projects"]
    assert default["slug"] == "default"
    enrolled = hub.client.post(f"/api/runners/{_RUNNER_ID}/enrollments")
    assert enrolled.status_code == 201, enrolled.text
    bearer = {"Authorization": f"Bearer {enrolled.json()['token']}"}

    with TestClient(hub.app, headers=bearer) as fleet:
        reply = HttpHubClient(fleet).register_runner("r-carried", "ws-carried", projects=None)

    assert reply.runner_id == _RUNNER_ID
    assert reply.projects is not None
    assert [(p.entry, p.project_id, p.slug) for p in reply.projects] == [
        (default["project_id"], default["project_id"], "default")
    ]
    detail = hub.client.get(f"/api/runners/{_RUNNER_ID}")
    assert detail.status_code == 200, detail.text
    assert [(p["entry"], p["project_id"]) for p in detail.json()["projects"]] == [
        (default["project_id"], default["project_id"])
    ]

    ingested = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    peeked = hub.client.post(
        "/api/fleet/queue/peek", json={"capabilities": [{"harness_id": "claude", "default": True}]}, headers=bearer
    )
    assert peeked.status_code == 200, peeked.text
    assert [(e["chunk_id"], e["project_slug"]) for e in peeked.json()["entries"]] == [(chunk_id, "default")]


def test_a_carried_store_applies_its_own_export_without_a_change(tmp_path: Path) -> None:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(_NOW)).runner.upgrade(_BEFORE_TENANTS)
    engine = create_engine_from_url(db_url)
    try:
        with engine.begin() as conn:
            for statement in _CARRIED_SEED:
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()

    hub = build_hub(tmp_path, repositories=(), link=False)
    assert hub.client.post("/api/graphs/sync").status_code == 200
    exported = hub.client.get("/api/config/export")
    assert exported.status_code == 200
    document = exported.json()
    assert [(p["slug"], p["sources"], p["repositories"]) for p in document["projects"]] == [
        ("default", ["acme"], ["acme-blizzard", "acme-widget"])
    ]
    assert [r["name"] for r in document["routines"]] == ["code-health"]

    def changes() -> list[dict[str, object]]:
        return hub.client.get("/api/config/changes", params={"limit": 200}).json()["changes"]

    before = changes()
    applied = hub.client.post("/api/config/apply", json=document)

    assert applied.status_code == 200, applied.text
    outcomes = applied.json()["outcomes"]
    assert outcomes
    assert Counter(outcome["op"] for outcome in outcomes) == {"unchanged": len(outcomes)}
    assert changes() == before
    assert hub.client.get("/api/config/export").content == exported.content


def test_a_fresh_store_starts_with_the_one_default_tenant(tmp_path: Path) -> None:
    config = init_hub(tmp_path)
    engine = create_engine_from_url(config.db_url)
    try:
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_the_first_tenant_is_stamped_by_the_clock_the_migration_was_given(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    at = datetime(2026, 3, 4, 5, 6, 7, 890000, tzinfo=UTC)
    try:
        Migrations(HubConfig(root=tmp_path, db_url=db_url), FixedClock(at)).runner.upgrade(_TENANTS)

        with engine.connect() as conn:
            tenant_id, created_at = conn.execute(
                sa.select(sa.column("tenant_id"), sa.column("created_at", UtcDateTime)).select_from(sa.table("tenants"))
            ).one()
        assert created_at == at
        parsed = Id.parse(tenant_id)
        assert parsed is not None and parsed.minted_at == at
    finally:
        engine.dispose()


def test_a_hub_upgrade_through_the_tenants_revision_without_a_clock_fails_naming_the_fix(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = MigrationRunner(script_location=MIGRATIONS_DIR, url=db_url)
    try:
        runner.upgrade(_BEFORE)

        with pytest.raises(MigrationClockMissingError, match="migrate through MigrationRunner with a clock"):
            runner.upgrade(_TENANTS)
        assert runner.current_revision() == _BEFORE
    finally:
        engine.dispose()


def test_a_carried_over_store_keeps_every_row_and_gains_one_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement), {"at": _T0})
        before = _row_counts(engine)

        runner.upgrade(_TENANTS)

        after = _row_counts(engine)
        assert {table: after[table] for table in before} == before
        assert {table: after[table] for table in _ADDED} == {
            "tenants": 1,
            "tenant_deletions": 0,
            "invitations": 0,
            "invitation_facts": 0,
            "hub_event_log": 0,
        }
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_downgrade_removes_exactly_what_the_revision_added(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            for statement in _SEED:
                conn.execute(sa.text(statement), {"at": _T0})
        schema_before, counts_before = _schema(engine), _row_counts(engine)

        runner.upgrade(_TENANTS)
        assert _tables(engine) - set(schema_before) == _ADDED

        runner.downgrade(_BEFORE)
        assert _schema(engine) == schema_before
        assert _row_counts(engine) == counts_before
    finally:
        engine.dispose()


def test_a_rerun_after_downgrade_mints_a_fresh_single_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_TENANTS)
        [(first, *_)] = _tenant_rows(engine)
        runner.downgrade(_BEFORE)
        runner.upgrade(_TENANTS)
        [(second, *_)] = _tenant_rows(engine)
        assert first != second
        _assert_the_first_tenant(engine)
    finally:
        engine.dispose()


def test_upgrade_creates_only_what_is_missing_and_never_a_second_tenant(tmp_path: Path) -> None:
    """Re-entry: a store already holding the tenant record (an interrupted earlier run) is completed,
    not duplicated."""
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        with engine.begin() as conn:
            conn.execute(
                sa.text(
                    "CREATE TABLE tenants (tenant_id VARCHAR NOT NULL PRIMARY KEY, name VARCHAR NOT NULL,"
                    " created_at DATETIME NOT NULL, created_by VARCHAR NOT NULL)"
                )
            )
            conn.execute(
                sa.text("INSERT INTO tenants VALUES ('ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B', 'default', :at, 'migration')"),
                {"at": _T0},
            )

        runner.upgrade(_TENANTS)

        assert _tables(engine) >= _ADDED
        assert [row[0] for row in _tenant_rows(engine)] == ["ten_01J9Z3M0P8QK7V2S4W6X8Y0A1B"]
    finally:
        engine.dispose()


def test_the_tenant_tables_take_their_declared_shape(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    try:
        migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade(_TENANTS)
        inspector = sa.inspect(engine)

        def columns(table: str) -> dict[str, bool]:
            return {str(c["name"]): bool(c["nullable"]) for c in inspector.get_columns(table)}

        def foreign_keys(table: str) -> dict[str | None, tuple[tuple[str, ...], str]]:
            return {
                fk["name"]: (tuple(fk["constrained_columns"]), fk["referred_table"])
                for fk in inspector.get_foreign_keys(table)
            }

        def indexes(table: str) -> dict[str | None, tuple[str | None, ...]]:
            return {i["name"]: tuple(i["column_names"]) for i in inspector.get_indexes(table)}

        assert columns("tenants") == {"tenant_id": False, "name": False, "created_at": False, "created_by": False}
        assert inspector.get_pk_constraint("tenants")["constrained_columns"] == ["tenant_id"]
        assert not inspector.get_unique_constraints("tenants")
        assert not [i for i in inspector.get_indexes("tenants") if i["unique"]]

        assert columns("tenant_deletions") == {
            "id": False,
            "tenant_id": False,
            "name": False,
            "deleted_at": True,
            "deleted_by": False,
            "rows_removed": True,
        }
        assert not foreign_keys("tenant_deletions")
        assert indexes("tenant_deletions") == {"ix_tenant_deletions_tenant_id": ("tenant_id",)}

        assert columns("invitations") == {
            "invitation_id": False,
            "tenant_id": False,
            "email": False,
            "role": False,
            "token_hash": False,
            "created_by": False,
            "created_at": False,
            "expires_at": False,
        }
        assert foreign_keys("invitations") == {"fk_invitations_tenant_id": (("tenant_id",), "tenants")}
        assert indexes("invitations") == {
            "ix_invitations_tenant_id": ("tenant_id",),
            "ix_invitations_token_hash": ("token_hash",),
        }

        assert columns("invitation_facts") == {
            "id": False,
            "tenant_id": False,
            "invitation_id": False,
            "kind": False,
            "actor_user_id": False,
            "recorded_at": False,
        }
        assert foreign_keys("invitation_facts") == {
            "fk_invitation_facts_tenant_id": (("tenant_id",), "tenants"),
            "fk_invitation_facts_invitation_id": (("invitation_id",), "invitations"),
        }
        assert {u["name"] for u in inspector.get_unique_constraints("invitation_facts")} == {
            "uq_invitation_facts_invitation_id"
        }
        assert {c["name"] for c in inspector.get_check_constraints("invitation_facts")} == {"ck_invitation_facts_kind"}
        assert indexes("invitation_facts") == {"ix_invitation_facts_tenant_id": ("tenant_id",)}

        assert columns("hub_event_log") == {
            "id": False,
            "recorded_at": False,
            "severity": False,
            "kind": False,
            "message": False,
            "detail": True,
        }
        assert indexes("hub_event_log") == {"ix_hub_event_log_recorded_at": ("recorded_at",)}
    finally:
        engine.dispose()


def test_the_global_list_names_exactly_the_tables_that_carry_no_tenant_key() -> None:
    assert (
        frozenset(
            {
                "tenants",
                "tenant_deletions",
                "users",
                "identities",
                "sessions",
                "auth_state",
                "auth_facts",
                "superuser_bootstrap",
                "hub_admin_facts",
                "config_import_facts",
                "membership_facts",
                "hub_event_log",
                "trace_cursor",
                "egress_cursor",
            }
        )
        == GLOBAL_TABLES
    )


def test_a_carried_over_store_reaches_head_with_every_row_in_the_first_tenant(tmp_path: Path) -> None:
    db_url, engine = _store(tmp_path)
    runner = migration_runner(HubConfig(root=tmp_path, db_url=db_url))
    try:
        runner.upgrade(_BEFORE)
        seeded = _seed_every_tenant_owned_table(engine)
        before = _row_counts(engine)

        runner.upgrade("head")

        [(tenant_id, *_)] = _tenant_rows(engine)
        assert _keyed_tables(engine) == dict.fromkeys(_tables(engine) - GLOBAL_TABLES, True)
        assert seeded == set(before) - GLOBAL_TABLES
        after = _row_counts(engine)
        after["config_changes"] -= _project_change_rows(engine)
        assert {table: after[table] for table in seeded} == {table: before[table] for table in seeded}
        assert {table: set(_tenants_of(engine, table)) for table in seeded} == {table: {tenant_id} for table in seeded}
        assert _tenant_keys(engine) == {
            table: (False, f"fk_{table}_tenant_id") for table in _tables(engine) - GLOBAL_TABLES
        }
        assert _foreign_key_violations(engine) == []
    finally:
        engine.dispose()
