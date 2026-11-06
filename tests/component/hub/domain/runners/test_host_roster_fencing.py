"""A host's roster against its retirement (component tier): no write removes an unretired runner from a retired host's
roster or adds a live runner to a retired host, and a host retire acts on the roster its own fact left.

Each race test forces its interleaving with a one-shot hook on the write that loses it: the competing request runs to
completion just before that write lands, after the losing request has decided."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy import func

from blizzard.hub.domain.runners.host import IWriteHostRegistry
from blizzard.hub.domain.runners.registration import RunnerRegistration
from blizzard.hub.store import schema as s
from tests.support.scaffolding import HubHarness, build_hub, emitted_events, hub_store_connections, seed_runner


class _Crash(Exception):
    """The hub process dying just after a write committed."""


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _add_host(hub: HubHarness, name: str = "box") -> dict[str, Any]:
    return _ok(hub.client.post("/api/hosts", json={"name": name}), 201)


def _adopt(hub: HubHarness, host_id: str, runner_id: str) -> Any:
    return hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": [runner_id], "by": "op"})


def _release(hub: HubHarness, host_id: str, runner_id: str) -> Any:
    return hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": [runner_id], "by": "op"})


def _retire_host(hub: HubHarness, host_id: str) -> Any:
    return hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op", "force": True})


def _runner(hub: HubHarness, runner_id: str) -> RunnerRegistration:
    runner = hub.services.registry.get_runner(runner_id)
    assert runner is not None
    return runner


def _host_facts(hub: HubHarness, host_id: str) -> int:
    store = hub_store_connections(hub.engine)
    facts = s.host_lifecycle_facts
    with store.read("count_host_facts") as conn:
        return int(conn.execute(store.select(facts, func.count()).where(facts.c.host_id == host_id)).scalar_one())


def _once_before(
    monkeypatch: pytest.MonkeyPatch, target: object, name: str, competitor: Callable[[], None], *, nth: int = 1
) -> None:
    """Patch ``target.name`` so its ``nth`` call runs ``competitor`` to completion just before it runs; every other
    call, the competitor's own included, runs as it would."""
    original = getattr(target, name)
    calls = [0]

    def compete_first(*args: Any, **kwargs: Any) -> Any:
        calls[0] += 1
        if calls[0] == nth:
            competitor()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, compete_first)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


@pytest.fixture
def hosted(hub: HubHarness) -> str:
    """A host ``box`` with the live runner ``rn_a`` under it; its id."""
    host_id = str(_add_host(hub)["host_id"])
    seed_runner(hub, "rn_a")
    _ok(_adopt(hub, host_id, "rn_a"))
    return host_id


def test_a_release_after_a_crashed_retire_is_refused_until_the_rerun_finishes_it(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = cast(IWriteHostRegistry, hub.services.hosts).record_retirement

    def crash_after_commit(*args: Any, **kwargs: Any) -> Any:
        original(*args, **kwargs)
        raise _Crash

    monkeypatch.setattr(hub.services.hosts, "record_retirement", crash_after_commit)
    with pytest.raises(_Crash):
        _retire_host(hub, hosted)
    monkeypatch.undo()

    refused = _release(hub, hosted, "rn_a")

    assert refused.status_code == 409, refused.text
    assert hosted in refused.json()["detail"] and "re-run" in refused.json()["detail"]
    assert _runner(hub, "rn_a").host_id == hosted
    _ok(_retire_host(hub, hosted))
    assert _runner(hub, "rn_a").retired
    _ok(_release(hub, hosted, "rn_a"))
    assert _runner(hub, "rn_a").host_id is None
    _ok(hub.client.post("/api/runners/rn_a/reinstate", json={"by": "op"}))
    assert not _runner(hub, "rn_a").retired


def test_a_release_decided_before_the_host_retires_is_refused(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _once_before(monkeypatch, hub.services.hosts, "release", lambda: _ok(_retire_host(hub, hosted)))

    refused = _release(hub, hosted, "rn_a")

    assert refused.status_code == 409, refused.text
    runner = _runner(hub, "rn_a")
    assert runner.host_id == hosted and runner.retired


def test_an_adoption_decided_before_the_host_retires_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = _add_host(hub)["host_id"]
    seed_runner(hub, "rn_a")
    _once_before(monkeypatch, hub.services.hosts, "adopt", lambda: _ok(_retire_host(hub, host_id)))

    refused = _adopt(hub, host_id, "rn_a")

    assert refused.status_code == 409, refused.text
    assert "retired" in refused.json()["detail"]
    runner = _runner(hub, "rn_a")
    assert runner.host_id is None and not runner.retired


def test_a_runner_added_by_a_host_that_retires_before_the_write_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    added = _add_host(hub)
    host_id = added["host_id"]
    _once_before(monkeypatch, hub.services.registry, "add", lambda: _ok(_retire_host(hub, host_id)))

    refused = hub.client.post(
        "/api/fleet/host/runners", json={"name": "late"}, headers={"Authorization": f"Bearer {added['token']}"}
    )

    assert refused.status_code == 403, refused.text
    assert hub.services.registry.list_runners(include_retired=True, host_id=host_id) == []


@pytest.mark.parametrize("door", ["add", "adopt"])
def test_a_placement_of_a_runner_retired_before_the_write_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    seed_runner(hub, "rn_a")
    retire_runner = lambda: _ok(hub.client.post("/api/runners/rn_a/retire", json={"by": "op"}))  # noqa: E731
    _once_before(monkeypatch, hub.services.hosts, door, retire_runner)

    if door == "add":
        refused = hub.client.post("/api/hosts", json={"name": "box", "adopt": ["rn_a"]})
    else:
        refused = _adopt(hub, _add_host(hub)["host_id"], "rn_a")

    assert refused.status_code == 409, refused.text
    runner = _runner(hub, "rn_a")
    assert runner.host_id is None and runner.retired


def test_a_retire_acts_on_the_roster_its_fact_left_not_the_one_it_first_read(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _add_host(hub, "other")["host_id"]

    def move_to_other() -> None:
        _ok(_release(hub, hosted, "rn_a"))
        _ok(_adopt(hub, other, "rn_a"))

    _once_before(monkeypatch, hub.services.hosts, "record_retirement", move_to_other)

    retired = _retire_host(hub, hosted)

    assert retired.status_code == 200, retired.text
    assert retired.json()["host"]["retired"] is True
    runner = _runner(hub, "rn_a")
    assert runner.host_id == other and not runner.retired and runner.token_hash is not None


def test_two_first_retires_of_one_host_write_one_fact(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _once_before(monkeypatch, hub.services.hosts, "record_retirement", lambda: _ok(_retire_host(hub, hosted)))

    second = _retire_host(hub, hosted)

    assert second.status_code == 409, second.text
    assert _host_facts(hub, hosted) == 1


def test_a_runner_under_a_retired_host_is_not_reinstated(hub: HubHarness, hosted: str) -> None:
    _ok(_retire_host(hub, hosted))

    refused = hub.client.post("/api/runners/rn_a/reinstate", json={"by": "op"})

    assert refused.status_code == 409, refused.text
    assert hosted in refused.json()["detail"]
    assert _runner(hub, "rn_a").retired


def test_a_reinstate_decided_before_the_host_retires_is_refused(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok(hub.client.post("/api/runners/rn_a/retire", json={"by": "op"}))
    _once_before(monkeypatch, hub.services.registry, "record_lifecycle", lambda: _ok(_retire_host(hub, hosted)))

    refused = hub.client.post("/api/runners/rn_a/reinstate", json={"by": "op"})

    assert refused.status_code == 409, refused.text
    runner = _runner(hub, "rn_a")
    assert runner.retired and runner.host_id == hosted


def _last_event_id(hub: HubHarness) -> int:
    events = emitted_events(hub)
    return int(events[-1]["id"]) if events else 0


def _retired_frames(hub: HubHarness, since: int, *, event: str) -> list[dict[str, Any]]:
    """The ``event`` frames published after ``since`` that announce a retirement."""
    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == event]
    return [frame for frame in frames if frame.get("kind") == "retired"]


def test_a_runner_adopted_before_the_retire_lands_is_retired_and_published(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_runner(hub, "rn_b")
    _once_before(monkeypatch, hub.services.hosts, "record_retirement", lambda: _ok(_adopt(hub, hosted, "rn_b")))
    since = _last_event_id(hub)

    _ok(_retire_host(hub, hosted))

    assert _runner(hub, "rn_b").retired and _runner(hub, "rn_b").host_id == hosted
    assert {f["runner_id"] for f in _retired_frames(hub, since, event="runner-changed")} == {"rn_a", "rn_b"}
    assert len(_retired_frames(hub, since, event="host-changed")) == 1


def test_a_roster_runner_retired_before_the_loop_reaches_it_interrupts_the_retire(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    retire_runner = lambda: _ok(hub.client.post("/api/runners/rn_a/retire", json={"by": "other"}))  # noqa: E731
    # The host's own holdings read is the first; the second is the loop's retire of rn_a deciding, just before it
    # writes.
    _once_before(monkeypatch, hub.services.fleet, "holdings", retire_runner, nth=2)
    since = _last_event_id(hub)

    interrupted = _retire_host(hub, hosted)

    assert interrupted.status_code == 409, interrupted.text
    assert "rn_a" in interrupted.json()["detail"]
    assert _ok(hub.client.get(f"/api/hosts/{hosted}"))["retired"] is True
    assert _runner(hub, "rn_a").retired
    assert len(_retired_frames(hub, since, event="host-changed")) == 1
    # Only the competing retire wrote rn_a's fact, so only its frame announces it.
    assert [f["runner_id"] for f in _retired_frames(hub, since, event="runner-changed")] == ["rn_a"]


@pytest.mark.parametrize("door", ["operator", "host"])
def test_a_runner_retire_that_loses_its_fact_write_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch, door: str
) -> None:
    added = _add_host(hub)
    headers = {"Authorization": f"Bearer {added['token']}"}
    runner_id = _ok(hub.client.post("/api/fleet/host/runners", json={"name": "r"}, headers=headers), 201)["runner_id"]
    competitor = lambda: _ok(hub.client.post(f"/api/runners/{runner_id}/retire", json={"by": "other"}))  # noqa: E731
    # The retire's holdings read is the last read it decides on before its fact write.
    _once_before(monkeypatch, hub.services.fleet, "holdings", competitor)

    if door == "operator":
        refused = hub.client.post(f"/api/runners/{runner_id}/retire", json={"by": "op"})
    else:
        refused = hub.client.post(f"/api/fleet/host/runners/{runner_id}/retire", json={"by": "box"}, headers=headers)

    assert refused.status_code == 409, refused.text
    assert _runner(hub, runner_id).retired_by == "other"


def test_a_release_naming_a_runner_moved_to_another_host_meanwhile_releases_none(
    hub: HubHarness, hosted: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = _add_host(hub, "other")["host_id"]
    seed_runner(hub, "rn_b")
    _ok(_adopt(hub, hosted, "rn_b"))

    def move_b() -> None:
        _ok(_release(hub, hosted, "rn_b"))
        _ok(_adopt(hub, other, "rn_b"))

    _once_before(monkeypatch, hub.services.hosts, "release", move_b)

    refused = hub.client.post(f"/api/hosts/{hosted}/releases", json={"runner_ids": ["rn_a", "rn_b"], "by": "op"})

    assert refused.status_code == 404, refused.text
    assert _runner(hub, "rn_a").host_id == hosted
    assert _runner(hub, "rn_b").host_id == other
