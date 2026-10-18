"""A graduated directory still holding its pre-host ``runner.db`` (component tier): only ``migrate`` creates or adopts
the host store, so every other verb refuses to open a missing one rather than let SQLite create an empty file that
adoption would then mistake for the host's store."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.runner.host_layout import HostLayout, migrate_remedy
from tests.runner_init_fakes import FakeInitHub, init_runner
from tests.test_baseline_floor import make_tree

pytestmark = pytest.mark.component

_HUB = "http://hub.test:8421"


def _graduated_unadopted(tmp_path: Path, hub: FakeInitHub) -> Path:
    """A joined host directory whose store is back under the pre-host name, as a graduation leaves it."""
    root = tmp_path / "host"
    assert init_runner(hub).invoke(blizzard, ["runner", "init", str(root), "--hub", _HUB]).exit_code == 0
    layout = HostLayout(root)
    layout.store_path.rename(layout.legacy_store_path)
    return root


def _run(hub: FakeInitHub, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, list(args))


def test_absorbed_pre_host_store_is_refused_before_adoption(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    with closing(sqlite3.connect(layout.legacy_store_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = 'old_rev'")
        conn.commit()
    before = layout.legacy_store_path.read_bytes()
    import blizzard.runner.runtime as runtime

    monkeypatch.setattr(runtime, "MIGRATIONS_DIR", make_tree(tmp_path))
    result = _run(fake_init_hub, "runner", "migrate", "--dir", str(root))
    assert result.exit_code != 0
    assert "old_rev" in result.output and "v0.2.0" in result.output
    assert str(layout.legacy_store_path) in result.output
    assert "Traceback" not in result.output
    assert layout.legacy_store_path.read_bytes() == before
    assert not layout.store_path.exists()


@pytest.mark.parametrize(
    "verb",
    [
        ["runner", "host", "--check"],
        ["runner", "host"],
        ["runner", "tick"],
        ["dev", "check-invariants", "--runner-dir"],
        ["runner", "transcript", "backfill", "--dry-run"],
    ],
    ids=["host-check", "host", "tick", "check-invariants", "offline-read"],
)
def test_a_verb_other_than_migrate_refuses_an_unadopted_directory_and_creates_no_host_store(
    tmp_path: Path, fake_init_hub: FakeInitHub, verb: list[str]
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    args = [*verb, str(root)] if verb[-1] == "--runner-dir" else [*verb, "--dir", str(root)]

    result = _run(fake_init_hub, *args)

    assert result.exit_code != 0, result.output
    remedy = migrate_remedy(root)
    assert remedy in " ".join(result.output.split()) and "runner.db" in result.output
    assert not layout.store_path.exists()
    assert layout.legacy_store_path.exists()

    migrated = _run(fake_init_hub, *remedy.removeprefix("blizzard ").split())

    assert migrated.exit_code == 0, migrated.output
    assert layout.store_path.exists() and not layout.legacy_store_path.exists()
    with closing(sqlite3.connect(layout.store_path)) as conn:
        assert conn.execute("SELECT runner_id FROM runner_identity").fetchall() == [("rn_init1",)]


def test_migrate_refuses_a_stray_empty_host_store_beside_the_pre_host_one_and_deletes_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    sqlite3.connect(layout.store_path).close()
    assert layout.store_path.stat().st_size == 0

    refused = _run(fake_init_hub, "runner", "migrate", "--dir", str(root))

    assert refused.exit_code != 0
    assert "host.db" in refused.output and "runner.db" in refused.output and "remove it" in refused.output
    assert layout.store_path.exists() and layout.legacy_store_path.exists()


@pytest.mark.parametrize(
    "verb", [["runner", "store", "reset"], ["runner", "migrate", "--down", "base"]], ids=["store-reset", "migrate-down"]
)
def test_a_reset_or_downgrade_refuses_an_unadopted_directory_and_creates_no_host_store(
    tmp_path: Path, fake_init_hub: FakeInitHub, verb: list[str]
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    args = [*verb, str(root)] if verb[1] == "store" else [*verb, "--dir", str(root)]

    result = _run(fake_init_hub, *args)

    assert result.exit_code != 0, result.output
    assert migrate_remedy(root) in " ".join(result.output.split()) and "runner.db" in result.output
    assert not layout.store_path.exists()
    assert layout.legacy_store_path.exists()


def test_migrate_refuses_a_populated_host_store_beside_the_pre_host_one_and_changes_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    with closing(sqlite3.connect(layout.store_path)) as conn:
        conn.execute("CREATE TABLE marker (v TEXT)")
        conn.commit()
    before = {p.name: p.read_bytes() for p in layout.data_dir.iterdir() if p.is_file()}

    refused = _run(fake_init_hub, "runner", "migrate", "--dir", str(root))

    assert refused.exit_code != 0
    said = " ".join(refused.output.split())
    assert "host.db" in said and "runner.db" in said and "operator has to decide" in said
    assert {p.name: p.read_bytes() for p in layout.data_dir.iterdir() if p.is_file()} == before


def test_migrate_completes_an_adoption_a_crash_interrupted_after_the_database_moved(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _graduated_unadopted(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    wal = layout.legacy_store_path.with_name("runner.db-wal")
    wal.write_bytes(b"")
    layout.legacy_store_path.rename(layout.store_path)

    result = _run(fake_init_hub, "runner", "migrate", "--dir", str(root))

    assert result.exit_code == 0, result.output
    with closing(sqlite3.connect(layout.store_path)) as conn:
        assert conn.execute("SELECT runner_id FROM runner_identity").fetchall() == [("rn_init1",)]
