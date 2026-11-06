"""A runner retire's revocation and release pass act only on the retirement they belong to, and a reinstate only on
the retirement it observed (component tier).

Each race test forces its interleaving with a one-shot hook: the competing requests run to completion at the named
point, after the losing request has decided and before it writes."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.runners.registration import RunnerRegistration
from tests.support.scaffolding import HubHarness, build_hub, emitted_events, pointer_token

_RUNNER = "rn_a"

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


class _Crash(Exception):
    """The hub process dying at the hooked point."""


def _ok(response: Any, status: int = 200) -> Any:
    assert response.status_code == status, response.text
    return response.json()


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _runner(hub: HubHarness) -> RunnerRegistration:
    runner = hub.services.registry.get_runner(_RUNNER)
    assert runner is not None
    return runner


def _register(hub: HubHarness) -> None:
    body = {"runner_id": _RUNNER, "workspace_id": "ws-a", "capabilities": [{"harness_id": "claude", "default": True}]}
    _ok(hub.client.post("/api/fleet/runners", json=body), 201)


def _retire(hub: HubHarness, *, by: str = "op") -> Any:
    return hub.client.post(f"/api/runners/{_RUNNER}/retire", json={"by": by, "force": True})


def _reinstate(hub: HubHarness) -> Any:
    return hub.client.post(f"/api/runners/{_RUNNER}/reinstate", json={"by": "op"})


def _enroll(hub: HubHarness) -> str:
    return str(_ok(hub.client.post(f"/api/runners/{_RUNNER}/enrollments"), 201)["token"])


def _chunk(hub: HubHarness, ref: str) -> str:
    """A promoted chunk ready to claim."""
    chunk_id = str(
        _ok(hub.client.post("/api/chunks", json={"tokens": [pointer_token({"source": "default", "ref": ref})]}), 201)[
            "chunk_id"
        ]
    )
    _ok(hub.client.post(f"/api/chunks/{chunk_id}/promote"), 202)
    return chunk_id


def _claim(hub: HubHarness, chunk_id: str, headers: dict[str, str] | None = None) -> None:
    body = {"chunk_id": chunk_id, "runner_id": _RUNNER, "workspace_id": "ws-a", "environment_ids": ["e1"]}
    _ok(hub.client.post("/api/fleet/routes", json=body, headers=headers), 201)


def _route_runner(hub: HubHarness, chunk_id: str) -> str | None:
    route = _ok(hub.client.get(f"/api/chunks/{chunk_id}"))["route"]
    return None if route is None else str(route["runner_id"])


def _last_event_id(hub: HubHarness) -> int:
    events = emitted_events(hub)
    return int(events[-1]["id"]) if events else 0


def _frames(hub: HubHarness, since: int, event: str) -> list[dict[str, Any]]:
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == event]


def _once(monkeypatch: pytest.MonkeyPatch, target: object, name: str, competitor: Callable[[], None]) -> None:
    """Patch ``target.name`` so its first call runs ``competitor`` to completion before it runs; every later call, the
    competitor's own included, runs as it would."""
    original = getattr(target, name)
    armed = [True]

    def compete_first(*args: Any, **kwargs: Any) -> Any:
        if armed:
            armed.clear()
            competitor()
        return original(*args, **kwargs)

    monkeypatch.setattr(target, name, compete_first)


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    """A hub with the delivery graph and the registered runner ``rn_a``."""
    hub = build_hub(tmp_path)
    _ok(hub.client.post("/api/graphs", json={"definition_yaml": _YAML}), 201)
    _register(hub)
    return hub


def test_a_stale_retire_rerun_leaves_a_rescued_runner_its_token_and_route(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok(_retire(hub))
    chunk_id = _chunk(hub, "700")
    tokens: list[str] = []

    def rescue() -> None:
        _ok(_reinstate(hub))
        tokens.append(_enroll(hub))
        _claim(hub, chunk_id, _bearer(tokens[0]))

    # The re-run decides on the retired registration its route loaded, then the rescue lands before its revocation.
    _once(monkeypatch, hub.services.fleet, "holdings", rescue)

    refused = _retire(hub, by="stale")

    assert refused.status_code == 409, refused.text
    assert not _runner(hub).retired
    _ok(hub.client.get("/api/fleet/identity", headers=_bearer(tokens[0])))
    assert _route_runner(hub, chunk_id) == _RUNNER


def test_a_stale_release_pass_leaves_a_rescued_runner_its_reclaimed_route(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = _chunk(hub, "701")
    _claim(hub, chunk_id)
    tokens: list[str] = []

    def rescue_and_reclaim() -> None:
        _ok(_reinstate(hub))
        tokens.append(_enroll(hub))
        _claim(hub, chunk_id, _bearer(tokens[0]))

    # The retire's fact and revocation have committed; the rescue reclaims the chunk before the release pass reaches it.
    _once(monkeypatch, hub.services.detach, "release_held", rescue_and_reclaim)
    since = _last_event_id(hub)

    refused = _retire(hub)

    assert refused.status_code == 409, refused.text
    assert not _runner(hub).retired
    _ok(hub.client.get("/api/fleet/identity", headers=_bearer(tokens[0])))
    assert _route_runner(hub, chunk_id) == _RUNNER
    # What the interrupted retire did commit is still announced: its fact.
    retired = [f for f in _frames(hub, since, "runner-changed") if f.get("kind") == "retired"]
    assert [f.get("by") for f in retired] == ["op"]


def test_a_reinstate_after_a_retire_stopped_before_its_release_pass_releases_the_leftover_route(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = _chunk(hub, "702")
    _claim(hub, chunk_id)

    def crash() -> None:
        raise _Crash

    _once(monkeypatch, hub.services.detach, "release_held", crash)
    with pytest.raises(_Crash):
        _retire(hub)
    monkeypatch.undo()
    assert _runner(hub).retired and _route_runner(hub, chunk_id) == _RUNNER
    since = _last_event_id(hub)

    _ok(_reinstate(hub))

    assert not _runner(hub).retired
    assert _route_runner(hub, chunk_id) is None
    detached = [f for f in _frames(hub, since, "chunk-changed") if f.get("cause") == "detached"]
    assert [f.get("chunk_id") for f in detached] == [chunk_id]


def test_a_reinstate_decided_before_a_reinstate_and_re_retire_leaves_the_runner_retired(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ok(_retire(hub))

    def reinstate_and_retire_again() -> None:
        _ok(_reinstate(hub))
        _ok(_retire(hub, by="later"))

    _once(monkeypatch, hub.services.registry, "record_lifecycle", reinstate_and_retire_again)

    refused = _reinstate(hub)

    assert refused.status_code == 409, refused.text
    runner = _runner(hub)
    assert runner.retired and runner.retired_by == "later"


def test_a_host_retire_leaves_a_runner_released_and_rescued_while_its_loop_ran(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = _ok(hub.client.post("/api/hosts", json={"name": "box"}), 201)["host_id"]
    _ok(hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": [_RUNNER], "by": "op"}))
    _ok(_retire(hub))
    tokens: list[str] = []

    def release_and_rescue() -> None:
        _ok(hub.client.post(f"/api/hosts/{host_id}/releases", json={"runner_ids": [_RUNNER], "by": "op"}))
        _ok(_reinstate(hub))
        tokens.append(_enroll(hub))

    # The host's fact has landed and its roster read holds the retired runner; the rescue lands before its turn.
    _once(monkeypatch, hub.services.fleet, "holdings", release_and_rescue)

    interrupted = hub.client.post(f"/api/hosts/{host_id}/retire", json={"by": "op", "force": True})

    assert interrupted.status_code == 409, interrupted.text
    assert _ok(hub.client.get(f"/api/hosts/{host_id}"))["retired"] is True
    runner = _runner(hub)
    assert not runner.retired and runner.host_id is None
    _ok(hub.client.get("/api/fleet/identity", headers=_bearer(tokens[0])))


def _once_after(monkeypatch: pytest.MonkeyPatch, target: object, name: str, competitor: Callable[[], None]) -> None:
    """Patch ``target.name`` so its first call runs, then ``competitor`` to completion before it returns; every later
    call, the competitor's own included, runs as it would."""
    original = getattr(target, name)
    armed = [True]

    def compete_after(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if armed:
            armed.clear()
            competitor()
        return result

    monkeypatch.setattr(target, name, compete_after)


def test_a_reinstate_refused_after_its_release_pass_still_announces_the_release(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    chunk_id = _chunk(hub, "703")
    _claim(hub, chunk_id)

    def crash() -> None:
        raise _Crash

    _once(monkeypatch, hub.services.detach, "release_held", crash)
    with pytest.raises(_Crash):
        _retire(hub)
    monkeypatch.undo()

    def reinstate_and_retire_again() -> None:
        _ok(_reinstate(hub))
        _ok(_retire(hub, by="later"))

    # The reinstate's release of the leftover route commits; a reinstate and a re-retire land before its own fact.
    _once_after(monkeypatch, hub.services.detach, "release_held", reinstate_and_retire_again)
    since = _last_event_id(hub)

    refused = _reinstate(hub)

    assert refused.status_code == 409, refused.text
    runner = _runner(hub)
    assert runner.retired and runner.retired_by == "later"
    assert _route_runner(hub, chunk_id) is None
    detached = [f for f in _frames(hub, since, "chunk-changed") if f.get("cause") == "detached"]
    assert [f.get("chunk_id") for f in detached] == [chunk_id]


def test_a_token_revocation_decided_before_a_retire_and_reinstate_is_refused(
    hub: HubHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    _enroll(hub)
    tokens: list[str] = []

    def retire_and_rescue() -> None:
        _ok(_retire(hub))
        _ok(_reinstate(hub))
        tokens.append(_enroll(hub))

    _once(monkeypatch, hub.services.registry, "revoke_token", retire_and_rescue)

    refused = hub.client.post(f"/api/runners/{_RUNNER}/token-revocations", json={"by": "op"})

    assert refused.status_code == 409, refused.text
    _ok(hub.client.get("/api/fleet/identity", headers=_bearer(tokens[0])))
