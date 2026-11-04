"""The two ``GET /api/flags`` answers — the hub's and a runner host's — each over a served app, with the
registry's overrides applied."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import structlog
from fastapi.testclient import TestClient
from structlog.typing import EventDict

from blizzard.hub.composition import build_hub_flags
from blizzard.hub.config import HubConfig
from blizzard.runner.app import build_hosted_app
from blizzard.runner.composition import build_host_process
from blizzard.runner.host_config import BuiltRunner
from tests.config_inputs import load_host_config
from tests.host_files import write_minimal_host
from tests.support import build_hub
from tests.support_flag_registry import (
    _TOKEN,  # pyright: ignore[reportPrivateUsage]
    _fixture,  # pyright: ignore[reportPrivateUsage]
)


@pytest.fixture
def warnings() -> Iterator[list[EventDict]]:
    with structlog.testing.capture_logs() as captured:
        yield captured


def _ignored(captured: list[EventDict]) -> dict[str, str]:
    return {str(e["flag"]): str(e["reason"]) for e in captured if e.get("event") == "flag override ignored"}


def test_a_hub_over_the_packaged_registry_answers_an_empty_map(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/flags").json() == {"flags": {}, "overridden": []}
    health = hub.client.get("/api/health").json()
    assert health["overridden"] == []
    assert health["status"] == "ok"
    assert set(health) == {"status", "service", "version", "overridden"}


def test_a_hub_answers_its_own_scopes_and_lists_the_applied_overrides(
    tmp_path: Path, warnings: list[EventDict]
) -> None:
    overrides = {"hub-thing": True, "fleet-thing": False, "runner-thing": True, "old-thing": True, "mistyped": True}
    flags = build_hub_flags(HubConfig(root=tmp_path, db_url="sqlite://", flags=overrides, flag_registry=_fixture()))
    hub = build_hub(tmp_path, flags=flags)
    answer = hub.client.get("/api/flags").json()
    assert answer == {"flags": {"fleet-thing": False, "hub-thing": True}, "overridden": ["fleet-thing", "hub-thing"]}
    assert hub.client.get("/api/health").json()["overridden"] == answer["overridden"]
    assert set(_ignored(warnings)) == {"runner-thing", "old-thing", "mistyped"}


def test_a_host_over_the_packaged_registry_answers_an_empty_map_and_keeps_its_health(tmp_path: Path) -> None:
    write_minimal_host(tmp_path)
    config = load_host_config(tmp_path, port=0)
    built = [p for p in config.projections(_TOKEN) if isinstance(p, BuiltRunner)]
    host = build_host_process(config, built)
    hosted = build_hosted_app(config.host_view(), host)
    try:
        with TestClient(hosted.app) as client:
            assert client.get("/api/flags").json() == {"flags": {}, "overridden": []}
            assert set(client.get("/api/health").json()) == {"status", "service", "version"}
    finally:
        hosted.close()
        host.close()


def test_a_host_answers_its_runner_flags_and_warns_once_per_ignored_key(
    tmp_path: Path, warnings: list[EventDict]
) -> None:
    flags = "[flags]\nrunner-thing = false\nhub-thing = true\nfleet-thing = true\nold-thing = true\nmistyped = true\n"
    write_minimal_host(tmp_path, top=flags)
    config = load_host_config(tmp_path, port=0, flag_registry=_fixture())
    built = [p for p in config.projections(_TOKEN) if isinstance(p, BuiltRunner)]
    host = build_host_process(config, built)
    hosted = build_hosted_app(config.host_view(), host)
    try:
        with TestClient(hosted.app) as client:
            assert client.get("/api/flags").json() == {"flags": {"runner-thing": False}, "overridden": ["runner-thing"]}
        for runner in host.runners:
            assert runner.flags.current().on("runner-thing") is False
    finally:
        hosted.close()
        host.close()
    ignored = [e for e in warnings if e.get("event") == "flag override ignored"]
    assert sorted(str(e["flag"]) for e in ignored) == ["fleet-thing", "hub-thing", "mistyped", "old-thing"]
    assert all(e["log_level"] == "warning" for e in ignored)
