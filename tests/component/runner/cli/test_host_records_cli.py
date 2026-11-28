"""``blizzard runner workspace`` and ``subscription`` over a registered host directory: each verb through a running
host's routes, or into a stopped host's store holding its directory, saying which; a create the host file would refuse
at boot refused with nothing written; an idempotent retire and enable saying nothing was written; and neither the host
file nor ``.env`` written."""

from __future__ import annotations

import json
import threading
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest
import uvicorn

from blizzard.foundation.clock import SystemClock
from blizzard.runner.app import build_hosted_app
from blizzard.runner.cli.runtime import StoreHostedRunners, read_host_stores, seed_runners
from blizzard.runner.host_config import HostConfig
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.listeners import Listeners, Uds
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.support.composed_host import open_host
from tests.support.config_inputs import load_host_config
from tests.support.runner_add_retire import (
    _registered,  # pyright: ignore[reportPrivateUsage]
    _run,  # pyright: ignore[reportPrivateUsage]
)
from tests.support.runner_cli_home import _await_socket  # pyright: ignore[reportPrivateUsage]
from tests.support.runner_init_fakes import FakeInitHub

pytestmark = pytest.mark.usefixtures("short_host_sockets")


def _files(root: Path) -> tuple[bytes, bytes]:
    return HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes()


def _changes(root: Path, kind: str, key: str) -> list[tuple[int, str, str]]:
    config = load_host_config(root)
    with read_host_stores(config) as stores:
        rows = stores.settings.changes(limit=50, before=None, record_kind=kind, record_key=key)
    return [(row.revision, row.op, row.door) for row in rows]


@contextmanager
def _serving(root: Path) -> Generator[HostConfig]:
    """The host at ``root`` running, its routes over its socket."""
    config = load_host_config(root)
    ready, halted = seed_runners(config, clock=SystemClock())
    assert not halted
    opened = open_host(config, ready)
    graph = opened.host
    served = replace(ready[0].config, port=0)
    hosted = build_hosted_app(served, host=opened, host_config=config, source=StoreHostedRunners.over(config, graph))
    sockets = Listeners.of(served).bound()
    server = uvicorn.Server(uvicorn.Config(hosted.app, log_level="warning"))
    thread = threading.Thread(target=lambda: server.run(sockets=sockets), daemon=True)
    thread.start()
    try:
        _await_socket(served.socket_path)
        yield config
    finally:
        server.should_exit = True
        thread.join(timeout=10.0)
        Uds(served.socket_path).unlink()
        hosted.close()
        opened.close()


def _ok(hub: FakeInitHub, *args: str) -> str:
    result = _run(hub, *args)
    assert result.exit_code == 0, result.output
    return result.output


def test_a_stopped_hosts_workspace_takes_every_verb_into_its_store(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)
    where = ("--dir", str(root))
    b_root = str(tmp_path / "b")

    # Before any write the store holds no record; the reads show what the next start seeds, writing nothing.
    unseeded = json.loads(_ok(fake_init_hub, "workspace", "list", *where))["items"]
    assert [(item["workspace_id"], item["revision"]) for item in unseeded] == [("workspace-local", 1)]
    assert json.loads(_ok(fake_init_hub, "workspace", "get", "workspace-local", *where))["revision"] == 1
    assert _changes(root, "workspace", "workspace-local") == []

    created = _ok(fake_init_hub, "workspace", "create", "ws-b", 'provider="basic"', f'root="{b_root}"', *where)
    assert "workspace ws-b created at revision 1 in the stopped host's store" in created
    edited = _ok(fake_init_hub, "workspace", "edit", "ws-b", "max_environments=2", 'envs=["e1", "e2"]', *where)
    assert "edited at revision 2" in edited
    record = json.loads(_ok(fake_init_hub, "workspace", "get", "ws-b", *where))
    assert record == {
        "workspace_id": "ws-b",
        "max_environments": 2,
        "revision": 2,
        "state": "active",
        "settings": {"provider": "basic", "root": b_root, "max_environments": 2, "envs": ["e1", "e2"]},
    }
    assert json.loads(_ok(fake_init_hub, "workspace", "show", "ws-b", *where)) == record
    unset = _ok(fake_init_hub, "workspace", "edit", "ws-b", "--unset", "max_environments", *where)
    assert "edited at revision 3" in unset

    assert "retired at revision 4" in _ok(fake_init_hub, "workspace", "retire", "ws-b", *where)
    again = _ok(fake_init_hub, "workspace", "retire", "ws-b", *where)
    assert "already at revision 4; nothing was written" in again
    listed = json.loads(_ok(fake_init_hub, "workspace", "list", *where))["items"]
    assert [item["workspace_id"] for item in listed] == ["workspace-local"]
    retired = json.loads(_ok(fake_init_hub, "workspace", "list", "--retired", *where))["items"]
    assert [(item["workspace_id"], item["state"]) for item in retired] == [
        ("workspace-local", "active"),
        ("ws-b", "retired"),
    ]
    assert "enabled at revision 5" in _ok(fake_init_hub, "workspace", "enable", "ws-b", *where)

    assert _changes(root, "workspace", "ws-b") == [
        (5, "enable", "cli"),
        (4, "retire", "cli"),
        (3, "edit", "cli"),
        (2, "edit", "cli"),
        (1, "create", "cli"),
    ]
    # The file's workspace was seeded before the first write, as the host's next start would.
    assert _changes(root, "workspace", "workspace-local") == [(1, "seed", "seed")]
    assert _files(root) == before


def test_a_stopped_host_refuses_what_its_file_would_fail_at_boot(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    where = ("--dir", str(root))

    bad = _run(fake_init_hub, "workspace", "create", "ws-x", 'provider="nope"', *where)
    assert bad.exit_code != 0 and "provider" in bad.output
    unknown = _run(fake_init_hub, "workspace", "create", "ws-x", 'colour="blue"', *where)
    assert unknown.exit_code != 0 and "colour" in unknown.output
    not_toml = _run(fake_init_hub, "workspace", "create", "ws-x", "root=/no/quotes", *where)
    assert not_toml.exit_code != 0
    missing = _run(fake_init_hub, "workspace", "edit", "nope", "max_environments=2", *where)
    assert missing.exit_code != 0 and "no workspace 'nope'" in missing.output
    assert _changes(root, "workspace", "ws-x") == []

    _ok(fake_init_hub, "subscription", "create", "sub-a", 'name="A"', f'provider="{PROVIDER_ANTHROPIC}"', *where)
    immutable = _run(fake_init_hub, "subscription", "edit", "sub-a", 'provider="other"', *where)
    assert immutable.exit_code != 0 and "provider" in immutable.output
    assert [op for _, op, _ in _changes(root, "subscription", "sub-a")] == ["create"]
    view = json.loads(_ok(fake_init_hub, "subscription", "get", "sub-a", *where))
    assert (view["slug"], view["name"], view["revision"], view["spenders"]) == ("sub-a", "A", 1, [])


def test_a_running_hosts_records_take_every_verb_through_its_routes(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = _files(root)
    where = ("--dir", str(root))
    with _serving(root):
        created = _ok(
            fake_init_hub, "workspace", "create", "ws-b", 'provider="basic"', f'root="{tmp_path / "b"}"', *where
        )
        assert "workspace ws-b created at revision 1 through the running host" in created
        assert "edited at revision 2" in _ok(fake_init_hub, "workspace", "edit", "ws-b", "max_environments=2", *where)
        view = json.loads(_ok(fake_init_hub, "workspace", "get", "ws-b", *where))
        assert (view["workspace_id"], view["revision"], view["max_environments"]) == ("ws-b", 2, 2)
        assert "retired at revision 3" in _ok(fake_init_hub, "workspace", "retire", "ws-b", *where)
        assert "nothing was written" in _ok(fake_init_hub, "workspace", "retire", "ws-b", *where)
        assert [i["workspace_id"] for i in json.loads(_ok(fake_init_hub, "workspace", "list", *where))["items"]] == [
            "workspace-local"
        ]
        assert "enabled at revision 4" in _ok(fake_init_hub, "workspace", "enable", "ws-b", *where)
        refused = _run(fake_init_hub, "workspace", "create", "ws-x", 'provider="nope"', *where)
        assert refused.exit_code != 0 and "settings-invalid" in refused.output

        sub = _ok(
            fake_init_hub, "subscription", "create", "sub-a", 'name="A"', f'provider="{PROVIDER_ANTHROPIC}"', *where
        )
        assert "subscription sub-a created at revision 1 through the running host" in sub
        assert "edited at revision 2" in _ok(fake_init_hub, "subscription", "edit", "sub-a", 'name="Renamed"', *where)
        listed = json.loads(_ok(fake_init_hub, "subscription", "list", *where))["items"]
        assert ("sub-a", "Renamed") in [(item["slug"], item["name"]) for item in listed]

    assert [(op, door) for _, op, door in _changes(root, "workspace", "ws-b")] == [
        ("enable", "api"),
        ("retire", "api"),
        ("edit", "api"),
        ("create", "api"),
    ]
    assert _files(root) == before
