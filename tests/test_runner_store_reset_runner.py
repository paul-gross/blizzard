"""``blizzard runner store reset --runner`` over a host of two runners (component tier): the runner's rows leave for an
archive store beside ``host.db`` — exactly its rows, migrated to head — while its outbox counters and the other runner's
rows stay, and the host passes the invariant checker; refused, changing nothing, while the host holds the directory or
a pre-host daemon answers on its socket."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.cli.collaborators import CliCollaborators
from blizzard.cli.main import blizzard
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.cli import store as store_cli
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.runtime import sqlite_url
from blizzard.runner.store import MIGRATIONS_DIR
from tests.config_inputs import load_host_config
from tests.runner_ingest_support import (
    HUB,
    RUNNER_TABLES,
    contents,
    invariant_violations,
    ok,
    rows,
    run,
    seed_host,
    seed_runner,
)
from tests.runner_init_fakes import FakeInitHub

pytestmark = pytest.mark.component

_STAYING = "rn_init1"
_RESET = "rn_init2"


def _host(tmp_path: Path, hub: FakeInitHub) -> Path:
    """A registered host of two runners, ``runner-local`` and ``r2``, each with one row in every runner table."""
    root = tmp_path / "host"
    ok(hub, "init", str(root), "--hub", HUB)
    ok(hub, "add", "r2", "--project", "default", "--dir", str(root))
    ok(hub, "init", str(root), "--hub", HUB)
    store = create_engine_from_url(sqlite_url(HostLayout(root).store_path))
    seed_runner(store, _STAYING, tag="a")
    seed_runner(store, _RESET, tag="b", last_seq=7)
    seed_host(store)
    store.dispose()
    return root


def _archives(root: Path) -> list[Path]:
    return sorted(p for p in HostLayout(root).data_dir.iterdir() if p.name.startswith("host.db.r2."))


def test_reset_of_one_runner_archives_exactly_its_rows_and_keeps_its_counters(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _host(tmp_path, fake_init_hub)
    url = load_host_config(root).db_url
    host = create_engine_from_url(url)
    staying = {name: contents(host, name, runner_id=_STAYING) for name in RUNNER_TABLES}
    leaving = {name: contents(host, name, runner_id=_RESET) for name in RUNNER_TABLES}
    counters = rows(host, "outbox_counters", runner_id=_RESET)

    result = ok(fake_init_hub, "store", "reset", str(root), "--runner", "r2")

    [archive] = _archives(root)
    assert f"archived runner r2 ({_RESET}) to {archive}" in result.output
    assert not archive.with_name(archive.name + ".tmp").exists()
    assert not archive.with_name(archive.name + "-wal").exists()
    archived = create_engine_from_url(sqlite_url(archive))
    migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=sqlite_url(archive))
    assert migrations.current_revision() == migrations.script_head()
    for name in RUNNER_TABLES:
        assert contents(archived, name) == leaving[name], name
        assert contents(host, name, runner_id=_STAYING) == staying[name], name
        expected = counters if name == "outbox_counters" else []
        assert rows(host, name, runner_id=_RESET) == expected, name
    assert invariant_violations(host) == []
    assert invariant_violations(archived) == []
    archived.dispose()
    host.dispose()


@pytest.mark.parametrize("held_by", ["lock", "socket"])
def test_reset_of_one_runner_is_refused_while_the_host_runs_and_changes_nothing(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch, held_by: str
) -> None:
    root = _host(tmp_path, fake_init_hub)
    layout = HostLayout(root)
    before = layout.store_path.read_bytes()
    if held_by == "socket":
        monkeypatch.setattr(
            store_cli,
            "daemon_holding",
            lambda sock, _trace: f"a runner answers at {sock}" if sock == layout.legacy_socket_path else None,
        )
        result = run(fake_init_hub, "store", "reset", str(root), "--runner", "r2")
    else:
        with HostLock.acquire(layout):
            result = run(fake_init_hub, "store", "reset", str(root), "--runner", "r2")

    assert result.exit_code != 0
    assert "cannot reset runner r2" in result.output and "stop it first" in result.output
    assert _archives(root) == []
    assert layout.store_path.read_bytes() == before


def test_reset_of_a_runner_the_host_does_not_name_is_refused(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _host(tmp_path, fake_init_hub)

    result = run(fake_init_hub, "store", "reset", str(root), "--runner", "nobody")

    assert result.exit_code != 0
    assert "declares no runner 'nobody'" in result.output
    assert _archives(root) == []


def test_reset_names_a_runner_the_host_file_no_longer_declares_by_its_id(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _host(tmp_path, fake_init_hub)
    config = HostLayout(root).config_path
    declared = config.read_text()
    config.write_text(declared[: declared.rindex("[[runner]]")])
    assert [r.name for r in load_host_config(root).runners] == ["runner-local"]

    result = ok(fake_init_hub, "store", "reset", str(root), "--runner", _RESET)

    [archive] = sorted(p for p in HostLayout(root).data_dir.iterdir() if p.name.startswith(f"host.db.{_RESET}."))
    assert f"archived runner {_RESET} ({_RESET}) to {archive}" in result.output
    host = create_engine_from_url(load_host_config(root).db_url)
    assert rows(host, "runner_identity", runner_id=_RESET) == []
    host.dispose()


def test_reset_of_a_declared_runner_without_an_id_is_refused(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _host(tmp_path, fake_init_hub)
    config = HostLayout(root).config_path
    config.write_text(config.read_text().replace(f'id = "{_RESET}"\n', "", 1))
    assert [r.id for r in load_host_config(root).runners if r.name == "r2"] == [None]

    result = run(fake_init_hub, "store", "reset", str(root), "--runner", "r2")

    assert result.exit_code != 0
    assert "has no id" in result.output
    assert _archives(root) == []


def test_reset_is_refused_when_the_archive_it_would_write_exists(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    frozen = replace(CliCollaborators.production(), wall_clock=FixedClock(datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)))
    root = _host(tmp_path, fake_init_hub)
    args = ["runner", "store", "reset", str(root), "--runner", "r2"]
    first = CliRunner().invoke(blizzard, args, obj=frozen)
    assert first.exit_code == 0, first.output
    [archive] = _archives(root)
    before = archive.read_bytes()

    result = CliRunner().invoke(blizzard, args, obj=frozen)

    assert result.exit_code != 0
    assert f"cannot archive runner r2: {archive} already exists" in result.output
    assert _archives(root) == [archive] and archive.read_bytes() == before
