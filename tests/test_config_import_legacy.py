"""``blizzard hub config import-legacy`` against a fixture hub — what it writes, once, and what it refuses
(component tier, migrated sqlite-on-disk)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from blizzard.cli.main import blizzard
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.app import import_legacy_config
from blizzard.hub.config import ConfigError, HubConfig
from blizzard.hub.domain.config.carry_over import ImportStatus, LegacyImportRefused
from blizzard.hub.secrets import ENV_SECRET_KEY, secret_keys_dir
from blizzard.hub.store.schema import (
    artifacts,
    config_changes,
    config_import_facts,
    repositories,
    secret_lifecycle_facts,
    secrets,
    work_sources,
)
from tests.support import seed_chunk, seed_graph

pytestmark = pytest.mark.component

_AT = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
_BLOCKS = """
[[work_source]]
name = "blizzard"
provider = "github"
repo = "paul-gross/blizzard"
token_env = "BZ_SHARED_TOKEN"
annotate = true

[[work_source]]
name = "infra"
provider = "github"
repo = "paul-gross/blizzard-infra"
token_env = "BZ_SHARED_TOKEN"
"""
_ENVIRON = {
    "BZ_SHARED_TOKEN": "shared-tok",
    "BZ_FORGE_URL": "https://api.github.com",
    "BZ_FORGE_OWNER": "paul-gross",
    "BZ_FORGE_BASE_BRANCH": "master",
    "BZ_FORGE_TOKEN": "forge-tok",
}
#: An owned origin, a bare origin, and a second artifact on the owned origin's coordinate.
_COMMITS = (
    ("https://github.com/acme/widget.git", "widget"),
    (None, "gadget"),
    ("git@github.com:acme/widget.git", "widget"),
)
_TABLES = (secrets, work_sources, repositories, config_changes, config_import_facts)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> HubConfig:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    config = hub_runtime.init_environment(tmp_path / "hub")
    config.config_path.write_text(config.config_path.read_text() + _BLOCKS)
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.begin() as conn:
            seed_graph(conn, "gr_1", at=_AT)
            seed_chunk(conn, "ch_1", graph_id="gr_1", at=_AT)
            for index, (forge, repo) in enumerate(_COMMITS):
                conn.execute(
                    sa.insert(artifacts).values(
                        artifact_id=f"art_{index}",
                        chunk_id="ch_1",
                        node_id="nd_1",
                        node_name="build",
                        epoch=1,
                        name=repo,
                        kind="git_commit",
                        data="main:abc",
                        repo=repo,
                        forge=forge,
                        produced_at=_AT,
                        seq=index,
                    )
                )
    finally:
        engine.dispose()
    return config


def _rows(config: HubConfig) -> dict[str, list[dict[str, object]]]:
    engine = create_engine_from_url(config.db_url)
    try:
        with engine.connect() as conn:
            return {t.name: [dict(r._mapping) for r in conn.execute(sa.select(t)).all()] for t in _TABLES}
    finally:
        engine.dispose()


def test_the_import_writes_the_expected_records_change_rows_and_one_fact(hub: HubConfig) -> None:
    result = import_legacy_config(hub, _ENVIRON)

    assert result.status is ImportStatus.IMPORTED
    rows = _rows(hub)
    assert sorted(str(r["name"]) for r in rows["secrets"]) == ["bz-forge-token", "bz-shared-token"]
    assert all("tok" not in str(r["ciphertext"]) for r in rows["secrets"])
    assert {(r["name"], r["locator"], r["secret_name"], r["annotate"]) for r in rows["work_sources"]} == {
        ("blizzard", "paul-gross/blizzard", "bz-shared-token", True),
        ("infra", "paul-gross/blizzard-infra", "bz-shared-token", False),
    }
    assert {
        (r["name"], r["forge_api_url"], r["owner"], r["repo"], r["base_branch"], r["secret_name"])
        for r in rows["repositories"]
    } == {
        ("widget", "https://api.github.com", "acme", "widget", "master", "bz-forge-token"),
        ("gadget", "https://api.github.com", "paul-gross", "gadget", "master", "bz-forge-token"),
    }
    changes = rows["config_changes"]
    assert len(changes) == 6
    assert {(r["actor"], r["door"], r["op"]) for r in changes} == {("migration", "migration", "create")}
    (fact,) = rows["config_import_facts"]
    assert fact["actor"] == "migration"
    read = json.loads(str(fact["read"]))
    assert read["sources"] == ["blizzard", "infra"]
    assert read["variables"] == [
        "BZ_FORGE_URL",
        "BZ_FORGE_OWNER",
        "BZ_FORGE_BASE_BRANCH",
        "BZ_FORGE_TOKEN",
        "BZ_SHARED_TOKEN",
    ]
    assert "tok" not in str(fact["read"])


def test_a_second_run_writes_nothing_and_says_so(hub: HubConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _ENVIRON.items():
        monkeypatch.setenv(name, value)
    runner = CliRunner()

    first = runner.invoke(blizzard, ["hub", "config", "import-legacy", "--dir", str(hub.root)])
    assert first.exit_code == 0, first.output
    assert "created   repository widget" in first.output
    before = _rows(hub)

    second = runner.invoke(blizzard, ["hub", "config", "import-legacy", "--dir", str(hub.root)])

    assert second.exit_code == 0, second.output
    assert "already imported; nothing written" in second.output
    assert _rows(hub) == before


def test_a_record_the_store_already_holds_is_skipped_and_still_referenced(hub: HubConfig) -> None:
    import_legacy_config(hub, _ENVIRON)
    engine = create_engine_from_url(hub.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(sa.delete(config_import_facts))
    finally:
        engine.dispose()
    before = _rows(hub)

    result = import_legacy_config(hub, _ENVIRON)

    assert result.status is ImportStatus.IMPORTED
    assert result.outcomes and not any(outcome.created for outcome in result.outcomes)
    after = _rows(hub)
    assert {t: rows for t, rows in after.items() if t != "config_import_facts"} == {
        t: rows for t, rows in before.items() if t != "config_import_facts"
    }
    assert len(after["config_import_facts"]) == 1


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        ({**_ENVIRON, "BZ_SHARED_TOKEN": " "}, "BZ_SHARED_TOKEN"),
        ({k: v for k, v in _ENVIRON.items() if k != "BZ_SHARED_TOKEN"}, "BZ_SHARED_TOKEN"),
        ({k: v for k, v in _ENVIRON.items() if k != "BZ_FORGE_TOKEN"}, "BZ_FORGE_TOKEN"),
        ({k: v for k, v in _ENVIRON.items() if k != "BZ_FORGE_URL"}, "BZ_FORGE_URL"),
    ],
)
def test_a_refusal_names_the_variable_and_leaves_the_store_untouched(
    hub: HubConfig, environ: dict[str, str], named: str
) -> None:
    before = _rows(hub)

    with pytest.raises(LegacyImportRefused, match=named):
        import_legacy_config(hub, environ)

    assert _rows(hub) == before


def test_a_refused_write_rolls_back_every_earlier_write(hub: HubConfig) -> None:
    """A source naming a secret that exists only as retired passes the plan but is refused in the
    transaction — after the secrets were inserted, which roll back with it."""
    engine = create_engine_from_url(hub.db_url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.insert(secrets).values(
                    name="bz-shared-token",
                    ciphertext="x",
                    nonce="x",
                    key_id="k",
                    revision=1,
                    replaced_at=_AT,
                    replaced_by="op",
                    created_at=_AT,
                )
            )
            conn.execute(
                sa.insert(secret_lifecycle_facts).values(name="bz-shared-token", retired=True, set_at=_AT, set_by="op")
            )
    finally:
        engine.dispose()
    before = _rows(hub)

    with pytest.raises(LegacyImportRefused, match="retired"):
        import_legacy_config(hub, _ENVIRON)

    assert _rows(hub) == before


def test_no_legacy_keys_writes_nothing_not_even_a_fact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ENV_SECRET_KEY, raising=False)
    config = hub_runtime.init_environment(tmp_path / "fresh")

    result = import_legacy_config(config, {})

    assert result.status is ImportStatus.NOTHING_TO_IMPORT
    assert all(not rows for rows in _rows(config).values())


def test_a_missing_hub_key_refuses_without_minting_one(hub: HubConfig) -> None:
    keys = secret_keys_dir(hub.data_dir)
    for path in keys.iterdir():
        path.unlink()
    before = _rows(hub)

    with pytest.raises(ConfigError, match="blizzard hub init"):
        import_legacy_config(hub, _ENVIRON)

    assert list(keys.iterdir()) == []
    assert _rows(hub) == before
