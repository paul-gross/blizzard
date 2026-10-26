"""``blizzard hub config check-forges`` against a migrated store on disk (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from tests.config_inputs import init_hub
from tests.forge_check_support import seed_addresses

pytestmark = pytest.mark.component

_REFUSABLE_REPOSITORIES = (
    ("userful", "https://user@forge.example", "acme", "widget"),
    ("plain", "https://forge.example", "acme", "gadget"),
    ("spelled", "https://forge.example/", "acme", "gadget"),
)
_REFUSABLE_SOURCES = (
    ("noscheme", "ghe.acme", None),
    ("web-one", "https://ghe.example/api/v3", "https://ghe.example"),
    ("web-two", "https://ghe.example/api/v3", "https://other.example"),
)


def _check(directory: Path) -> Result:
    return CliRunner().invoke(blizzard, ["hub", "config", "check-forges", "--dir", str(directory)])


def _dump(db_url: str) -> list[tuple[str, list[tuple[object, ...]]]]:
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            names = sorted(sa.inspect(conn).get_table_names())
            return [(name, [tuple(row) for row in conn.execute(sa.text(f'SELECT * FROM "{name}"'))]) for name in names]
    finally:
        engine.dispose()


def test_a_seeded_store_names_every_record_and_exits_one_without_writing(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    seed_addresses(config.db_url, repository_rows=_REFUSABLE_REPOSITORIES, work_source_rows=_REFUSABLE_SOURCES)
    before = _dump(config.db_url)

    result = _check(config.root)

    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    for case, slugs in (
        ("needs-cleaning", "repository userful"),
        ("coordinate-collision", "repository plain, spelled"),
        ("not-an-origin", "work source noscheme"),
        ("web-base-disagreement", "work source web-one, web-two"),
    ):
        assert any(case in line and slugs in line for line in lines), result.output
    assert _dump(config.db_url) == before


def test_a_quiet_store_names_nothing_and_exits_zero(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    seed_addresses(
        config.db_url,
        repository_rows=(("local", "http://127.0.0.1:8123", "acme", "widget"),),
        work_source_rows=(("empty", "", None), ("unset", None, None)),
    )

    result = _check(config.root)

    assert result.exit_code == 0, result.output
    assert "no record" in result.output


def test_a_second_tenants_records_are_kept_apart_from_the_first(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO tenants (tenant_id, name, created_at, created_by) VALUES (:id, 'beta', :at, 't')"),
                {"id": "ten_beta", "at": "2026-10-06 09:00:00"},
            )
    finally:
        engine.dispose()
    seed_addresses(
        config.db_url, repository_rows=(("one", "https://forge.example", "acme", "widget"),), tenant="ten_beta"
    )
    seed_addresses(
        config.db_url,
        repository_rows=(("two", "https://forge.example/", "acme", "widget"),),
        tenant=_first_tenant(config.db_url),
    )

    result = _check(config.root)

    assert result.exit_code == 0, result.output


def _first_tenant(db_url: str) -> str:
    engine = create_engine_from_url(db_url)
    try:
        with engine.connect() as conn:
            return str(
                conn.execute(sa.text("SELECT tenant_id FROM tenants WHERE tenant_id != 'ten_beta'")).scalar_one()
            )
    finally:
        engine.dispose()


def test_a_store_with_a_forges_table_has_nothing_left_to_carry(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    seed_addresses(config.db_url, repository_rows=_REFUSABLE_REPOSITORIES)
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TABLE forges (forge_id TEXT)"))
    finally:
        engine.dispose()

    result = _check(config.root)

    assert result.exit_code == 0, result.output
    assert "nothing left to carry" in result.output


def test_a_directory_with_no_store_is_refused_and_creates_none(tmp_path: Path) -> None:
    config = init_hub(tmp_path / "hub")
    store = config.data_dir.parent
    for path in list(store.rglob("*.db*")):
        path.unlink()

    result = _check(config.root)

    assert result.exit_code == 1
    assert "nothing to check" in result.output
    assert not list(store.rglob("*.db*"))
