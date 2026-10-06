"""Every hub read view and SSE frame that shows a runner names it beside its id (component tier) —
the registry's latest registered name, read once per response rather than once per row, and
absent for an id the registry does not hold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.foundation.hub_event_types import HubEventType
from tests.support import HubHarness, build_hub, count_queries, emitted_events, ingest, runner_token, seed_runner

pytestmark = pytest.mark.component

_RUNNER = "rn_01J9Z3QK7M4X8V2N5R6T1W0YAB"


def _frames(hub: HubHarness, event_type: str, *, since: int) -> list[dict[str, object]]:
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == event_type]


def _claim(hub: HubHarness, runner_id: str) -> str:
    chunk_id = ingest(hub, [{"source": "default", "ref": runner_id}])
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def _push(hub: HubHarness, runner_id: str, kind: str, payload: dict[str, object]) -> None:
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": runner_id, "facts": [{"seq": 1, "kind": kind, "payload": payload}]},
    )
    assert resp.status_code == 200 and resp.json()["applied"] == [1], resp.text


def _report_event(hub: HubHarness, runner_id: str) -> None:
    _push(hub, runner_id, "event.recorded", {"severity": "warning", "kind": "attempt-failed", "message": "boom"})


def _summary(hub: HubHarness, chunk_id: str) -> dict[str, object]:
    [row] = [row for row in hub.client.get("/api/chunks").json()["chunks"] if row["chunk_id"] == chunk_id]
    return row


def test_the_chunk_reads_and_the_claim_frame_name_the_routed_runner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, name="r-claude")
    since = hub.events.latest_id()

    chunk_id = _claim(hub, _RUNNER)

    summary = _summary(hub, chunk_id)
    assert (summary["runner_id"], summary["runner_name"]) == (_RUNNER, "r-claude")
    route = hub.client.get(f"/api/chunks/{chunk_id}").json()["route"]
    assert (route["runner_id"], route["runner_name"]) == (_RUNNER, "r-claude")
    routed = [f for f in _frames(hub, HubEventType.CHUNK_CHANGED, since=since) if f.get("runner_id") == _RUNNER]
    assert routed
    assert all(frame["runner_name"] == "r-claude" for frame in routed)


def test_a_question_names_its_asking_runner_on_every_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, name="r-claude")
    chunk_id = _claim(hub, _RUNNER)

    _push(
        hub,
        _RUNNER,
        "question.asked",
        {
            "question_id": "qn_0",
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-0",
            "epoch": 1,
            "question": "Which base?",
            "options": ["main", "dev"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )

    [listed] = hub.client.get("/api/questions").json()
    assert (listed["runner_id"], listed["runner_name"]) == (_RUNNER, "r-claude")
    [detailed] = hub.client.get(f"/api/chunks/{chunk_id}").json()["questions"]
    assert detailed["runner_name"] == "r-claude"
    polled = hub.client.get("/api/fleet/questions/qn_0", params={"runner_id": _RUNNER}).json()
    assert polled["runner_name"] == "r-claude"


def test_a_registration_under_a_new_name_renames_the_runner_on_its_frame_and_every_read(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, name="r-claude")
    chunk_id = _claim(hub, _RUNNER)
    since = hub.events.latest_id()

    assert hub.app is not None
    registered = TestClient(hub.app).post(
        "/api/fleet/runners",
        json={"workspace_id": "w1", "name": "r-renamed"},
        headers={"Authorization": f"Bearer {runner_token(_RUNNER)}"},
    )
    assert registered.status_code == 201, registered.text

    assert _frames(hub, HubEventType.RUNNER_CHANGED, since=since) == [
        {"runner_id": _RUNNER, "runner_name": "r-renamed", "kind": "registered"}
    ]
    assert _summary(hub, chunk_id)["runner_name"] == "r-renamed"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["route"]["runner_name"] == "r-renamed"


def test_a_reported_event_names_its_runner_on_the_frame_the_event_feed_and_the_activity_feed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, name="r-claude")
    since = hub.events.latest_id()

    _report_event(hub, _RUNNER)

    [frame] = _frames(hub, HubEventType.EVENT_LOGGED, since=since)
    assert (frame["runner_id"], frame["runner_name"]) == (_RUNNER, "r-claude")
    [event] = hub.client.get("/api/events").json()["events"]
    assert (event["runner_id"], event["runner_name"]) == (_RUNNER, "r-claude")
    [logged] = [row for row in hub.client.get("/api/activity").json()["activity"] if row["runner_id"] == _RUNNER]
    assert logged["runner_name"] == "r-claude"


def test_pause_frames_name_the_runner_from_the_operator_brake_and_the_runner_s_own(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, _RUNNER, name="r-claude")
    since = hub.events.latest_id()

    assert hub.client.post(f"/api/runners/{_RUNNER}/pause", json={"by": "op"}).status_code == 200
    _push(hub, _RUNNER, "runner.locally_paused", {"by": "operator", "reason": "maintenance"})

    frames = _frames(hub, HubEventType.RUNNER_CHANGED, since=since)
    assert [(f["kind"], f["runner_name"]) for f in frames] == [("paused", "r-claude"), ("locally-paused", "r-claude")]


def test_an_id_the_registry_does_not_hold_reads_unnamed(tmp_path: Path) -> None:
    """An event recorded under an id no registration holds — and recorded without a name handed
    to it — renders with no name, on the frame and on the feed alike."""
    hub = build_hub(tmp_path)
    since = hub.events.latest_id()

    hub.services.event_log.record(
        kind="attempt-failed",
        runner_id="rn_gone",
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="boom",
        detail=None,
        at=hub.clock.now(),
    )

    [frame] = _frames(hub, HubEventType.EVENT_LOGGED, since=since)
    assert frame["runner_id"] == "rn_gone"
    assert "runner_name" not in frame
    [event] = hub.client.get("/api/events").json()["events"]
    assert (event["runner_id"], event["runner_name"]) == ("rn_gone", None)


def test_the_feeds_and_the_chunk_list_read_every_runner_name_at_once(tmp_path: Path) -> None:
    """One runner or three, each read issues the same statements: the names come from one
    batched registry read per response, never one per row."""
    hubs: dict[int, HubHarness] = {}
    for count in (1, 3):
        (tmp_path / str(count)).mkdir()
        hub = hubs[count] = build_hub(tmp_path / str(count))
        for i in range(count):
            runner_id = f"rn_{i}"
            seed_runner(hub, runner_id, name=f"runner-{i}")
            _claim(hub, runner_id)
            _report_event(hub, runner_id)

    for path in ("/api/chunks", "/api/events", "/api/activity"):
        counts = {
            count: count_queries(hub.engine, lambda hub=hub, path=path: hub.client.get(path).raise_for_status())
            for count, hub in hubs.items()
        }
        assert counts[1] == counts[3], path
    names = {row["runner_name"] for row in hubs[3].client.get("/api/events").json()["events"]}
    assert names == {"runner-0", "runner-1", "runner-2"}
