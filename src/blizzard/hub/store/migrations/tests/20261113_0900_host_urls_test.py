"""The host-urls revision adds the nullable ``hosts.urls`` column, leaves a pre-change host reading as its one ``url``,
re-runs idempotently, and downgrades exactly."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.runners.host import origins_of
from blizzard.hub.store import schema as s
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261113_0900_credentials"
_REVISION = "20261113_0900_host_urls"
_AT = datetime(2026, 11, 13, tzinfo=UTC)


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _columns(engine: sa.Engine) -> dict[str, bool]:
    return {c["name"]: bool(c["nullable"]) for c in sa.inspect(engine).get_columns("hosts")}


def _hosts(engine: sa.Engine, tenant: str) -> None:
    with engine.begin() as conn:
        for host_id, url in (("hs_a", "https://a.example"), ("hs_b", None)):
            conn.execute(
                sa.text("INSERT INTO hosts (host_id, name, added_at, tenant_id, url) VALUES (:id, :id, :at, :t, :url)"),
                {"id": host_id, "at": _AT, "t": tenant, "url": url},
            )


def test_a_pre_change_host_reads_as_its_one_url_or_none(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _hosts(engine, default_tenant(engine))

    runner.upgrade(_REVISION)

    assert _columns(engine)["urls"] is True
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT host_id, url, urls FROM hosts ORDER BY host_id")).all()
    assert [row.urls for row in rows] == [None, None]
    assert [origins_of(None, row.url) for row in rows] == [("https://a.example",), ()]


def test_the_new_column_matches_the_declared_schema(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    declared = create_engine_from_url(f"sqlite:///{tmp_path / 'declared.db'}")
    try:
        s.metadata.create_all(declared)
        assert _columns(engine)["urls"] == _columns(declared)["urls"]
    finally:
        declared.dispose()


def test_a_rerun_over_a_store_already_reshaped_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _REVISION)
    schema = _snapshot(engine)
    with engine.begin() as conn:
        conn.execute(sa.text(f"UPDATE alembic_version SET version_num = '{_PARENT}'"))

    runner.upgrade(_REVISION)

    assert _snapshot(engine) == schema


def test_the_downgrade_restores_the_store_exactly(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)
    _hosts(engine, default_tenant(engine))
    runner.upgrade(_REVISION)

    runner.downgrade(_PARENT)

    assert _snapshot(engine) == before
    assert "urls" not in _columns(engine)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT host_id FROM hosts ORDER BY host_id")).scalars().all() == ["hs_a", "hs_b"]
    runner.upgrade(_REVISION)
    assert "urls" in _columns(engine)
