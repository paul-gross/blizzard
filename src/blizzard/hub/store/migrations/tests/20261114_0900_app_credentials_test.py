"""The app credentials revision changes nothing on the way up, and its downgrade is refused — writing nothing — while a
``github.app-installation`` credential exists, passing once it is gone."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from tests.support.migrations.project_columns_migration import insert, query
from tests.support.migrations.store_migrations import schema_snapshot
from tests.support.scaffolding import default_tenant, migrate_to

pytestmark = [pytest.mark.component]

_PARENT = "20261113_1000_credential_carry_over"
_REVISION = "20261114_0900_app_credentials"
_AT = datetime(2026, 11, 1, tzinfo=UTC)


def _credential(tenant: str, slug: str, binding: str, settings: str, authority: str) -> dict[str, object]:
    return {
        "credential_id": f"cred_{slug}",
        "slug": slug,
        "name": slug,
        "forge_id": "frg_a",
        "binding": binding,
        "secret_name": "key",
        "settings": settings,
        "authority": authority,
        "revision": 1,
        "created_at": _AT,
        "created_by": "op",
        "tenant_id": tenant,
    }


def _seed(engine: sa.Engine, tenant: str) -> None:
    insert(
        engine,
        "forges",
        [
            {
                "forge_id": "frg_a",
                "slug": "fa",
                "name": "fa",
                "kind": "github",
                "api_base": "https://fa.example/api",
                "web_base": None,
                "revision": 1,
                "created_at": _AT,
                "created_by": "op",
                "tenant_id": tenant,
            }
        ],
    )
    insert(engine, "secrets", [{"tenant_id": tenant, "name": "key"}])
    insert(
        engine,
        "credentials",
        [
            _credential(tenant, "token", "github.token", "{}", "secret/key"),
            _credential(
                tenant,
                "app",
                "github.app-installation",
                '{"app_id": 12, "installation_id": 34}',
                "app/12/installation/34",
            ),
        ],
    )


def _version(engine: sa.Engine) -> str:
    return str(query(engine, "SELECT version_num FROM alembic_version")[0][0])


def _snapshot(engine: sa.Engine) -> dict[str, object]:
    return schema_snapshot(engine, exemptions={}, name_exemptions={})


def _rows(engine: sa.Engine) -> list[tuple[object, ...]]:
    return [tuple(r) for r in query(engine, "SELECT credential_id, binding, settings FROM credentials ORDER BY 1")]


def test_the_upgrade_changes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    before = _snapshot(engine)

    runner.upgrade(_REVISION)

    assert _version(engine) == _REVISION
    assert _snapshot(engine) == before


def test_the_downgrade_is_refused_naming_an_app_credential_and_writes_nothing(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    tenant = default_tenant(engine)
    _seed(engine, tenant)
    runner.upgrade(_REVISION)
    rows = _rows(engine)

    with pytest.raises(RuntimeError) as refused:
        runner.downgrade(_PARENT)

    assert f"({tenant}): credential app" in str(refused.value)
    assert "credential token" not in str(refused.value)
    assert _version(engine) == _REVISION
    assert _rows(engine) == rows


def test_the_downgrade_passes_once_no_app_credential_remains(tmp_path: Path) -> None:
    runner, engine = migrate_to(tmp_path, _PARENT)
    _seed(engine, default_tenant(engine))
    runner.upgrade(_REVISION)
    with engine.begin() as connection:
        connection.execute(sa.text("DELETE FROM credentials WHERE binding = 'github.app-installation'"))

    runner.downgrade(_PARENT)

    assert _version(engine) == _PARENT
    assert [r[0] for r in _rows(engine)] == ["cred_token"]
