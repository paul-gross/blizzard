"""A host's start seeds a record for each ``[[workspace]]`` and ``[[subscription]]`` it holds none of, and resolves its
workspaces and subscriptions from its records from then on."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.settings_record.refusals import SettingsInvalid
from blizzard.runner.cli.runtime import seed_host
from blizzard.runner.composition import build_host_record_services, host_record_reads
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_records.model import HostRecord, RecordKind
from blizzard.runner.host_settings_reader import HostSettingsReader
from blizzard.runner.stores import HostStores
from tests.support.config_inputs import FLAG_REGISTRY, STRICT
from tests.support.host_files import write_minimal_host
from tests.support.runner_fakes import make_host_stores, make_store

_AT = datetime(2026, 11, 20, tzinfo=UTC)
_CLOCK = FixedClock(_AT)
_SUBSCRIPTION = '[[subscription]]\nslug = "team"\nname = "Team"\nprovider = "anthropic"\n'


def _host(tmp_path: Path, *, workspace: str = "", top: str = "") -> tuple[HostConfig, HostStores]:
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    write_minimal_host(tmp_path, top=f'db_url = "{url}"\n{top}', workspace=workspace)
    stores = make_host_stores(make_store(url), _CLOCK)
    return _load(tmp_path), stores


def _load(root: Path) -> HostConfig:
    return HostConfig.load(root, flag_registry=FLAG_REGISTRY, strict=STRICT, defer_tool_agreement=True)


def test_each_table_is_seeded_whole_once_under_the_seeds_door(tmp_path: Path) -> None:
    config, stores = _host(tmp_path, workspace='root = "/srv/a"\nprovider = "basic"', top=_SUBSCRIPTION)

    effective = seed_host(config, {}, clock=_CLOCK)

    assert stores.workspaces.records() == (
        HostRecord(RecordKind.WORKSPACE, "workspace-local", 1, {"root": "/srv/a", "provider": "basic"}),
    )
    assert stores.subscriptions.records() == (
        HostRecord(RecordKind.SUBSCRIPTION, "team", 1, {"name": "Team", "provider": "anthropic"}),
    )
    changes = stores.settings.changes(limit=10)
    assert {(c.record_kind, c.record_key, c.op, c.door, c.actor, c.revision) for c in changes} == {
        ("workspace", "workspace-local", "seed", "seed", "seed", 1),
        ("subscription", "team", "seed", "seed", "seed", 1),
    }
    assert [workspace.id for workspace in effective.workspaces] == ["workspace-local"]
    assert [subscription.slug for subscription in effective.subscriptions] == ["team"]


def test_a_workspace_with_a_record_is_untouched_by_a_later_start(tmp_path: Path) -> None:
    config, stores = _host(tmp_path, workspace='root = "/srv/a"\nprovider = "basic"')
    seed_host(config, {}, clock=_CLOCK)
    write_minimal_host(
        tmp_path, top=f'db_url = "{config.db_url}"', workspace='root = "/srv/elsewhere"\nprovider = "basic"'
    )

    effective = seed_host(_load(tmp_path), {}, clock=_CLOCK)

    held = stores.workspaces.record("workspace-local")
    assert held is not None
    assert (held.revision, held.settings["root"]) == (1, "/srv/a")
    assert len(stores.settings.changes(limit=10, record_kind="workspace")) == 1
    assert effective.workspaces[0].root == "/srv/a"


def test_a_workspace_prompt_override_becomes_the_seeded_prompt(tmp_path: Path) -> None:
    config, stores = _host(
        tmp_path, workspace='provider = "basic"\nprompt = "from the file"\nprompt_file = "p.md"\nprompt_package = "x"'
    )
    stores.workspace_prompt.set_workspace_prompt("workspace-local", prompt="from the route", at=_AT)

    effective = seed_host(config, {}, clock=_CLOCK)

    held = stores.workspaces.record("workspace-local")
    assert held is not None
    assert held.settings == {"provider": "basic", "prompt": "from the route"}
    workspace = effective.workspaces[0]
    assert (workspace.prompt, workspace.prompt_file, workspace.prompt_package) == ("from the route", "", "")


def test_the_reader_resolves_workspaces_from_records_and_its_version_moves_with_them(tmp_path: Path) -> None:
    config, stores = _host(tmp_path, workspace='root = "/srv/a"\nprovider = "basic"')
    seed_host(config, {}, clock=_CLOCK)
    reader = HostSettingsReader(config, stores.settings, host_record_reads(stores))
    service = build_host_record_services(config, stores, clock=_CLOCK)[RecordKind.WORKSPACE]
    before = reader.snapshot_record()

    service.create("ws-b", {"root": "/srv/b", "provider": "basic"}, actor="ada", door="api")
    service.retire(service.held("workspace-local"), actor="ada", door="api")
    after = reader.snapshot_record()

    assert after.revision == before.revision
    assert after.version() != before.version()
    assert [workspace.id for workspace in reader.resolve(after).workspaces] == ["ws-b"]
    assert [workspace.id for workspace in reader.resolve(before).workspaces] == ["workspace-local"]


def test_a_record_write_is_judged_by_the_host_files_parse(tmp_path: Path) -> None:
    config, stores = _host(tmp_path, workspace='root = "/srv/a"\nprovider = "basic"')
    seed_host(config, {}, clock=_CLOCK)
    service = build_host_record_services(config, stores, clock=_CLOCK)[RecordKind.WORKSPACE]

    with pytest.raises(SettingsInvalid) as provider:
        service.create("ws-b", {"root": "/srv/b", "provider": "nope"}, actor="ada", door="api")
    with pytest.raises(SettingsInvalid) as cap:
        service.edit(service.held("workspace-local"), {"max_environments": 0}, if_match=1, actor="ada", door="api")
    with pytest.raises(SettingsInvalid) as shared:
        service.create("ws-c", {"root": "/srv/a", "provider": "basic"}, actor="ada", door="api")

    assert "provider" in provider.value.errors
    assert "max_environments" in cap.value.errors
    assert shared.value.errors
    assert [record.key for record in stores.workspaces.records()] == ["workspace-local"]
