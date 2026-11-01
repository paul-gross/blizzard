"""Observability reads under a project lens (component tier): ``?project=`` on every ``/api/analytics/*`` read,
both NDJSON streams included, narrows it to the facts of one project's chunks; ``GET /api/events`` and
``GET /api/activity`` keep a row naming a chunk of the project, a row naming only a runner that serves it, and a
row naming neither. Without ``?project=`` each read covers the whole tenant; an unknown project answers ``422``.
A question carries its chunk's project: its listed row names the chunk, and its frames are stamped with it."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.event_log import EventLogSeverity
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store.internal.chunk_events_store import ChunkEventsStore
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import (
    HubHarness,
    build_hub,
    claim_route,
    emitted_events,
    hub_store_connections,
    ingest,
    link_project,
    seed_project,
    seed_runner,
)
from tests.test_analytics_events_api import _record, _tool_turn

pytestmark = pytest.mark.component

_ANALYTICS_READS = (
    "/api/analytics/events",
    "/api/analytics/events/ndjson",
    "/api/analytics/counts/files",
    "/api/analytics/counts/skills",
    "/api/analytics/counts/agent-types",
    "/api/analytics/counts/nodes",
    "/api/analytics/durations/nodes",
    "/api/analytics/durations/graphs",
    "/api/analytics/spend/nodes",
    "/api/analytics/spend/graphs",
    "/api/analytics/spend/chunks",
    "/api/analytics/spend/chunks/ndjson",
    "/api/analytics/outcomes/nodes",
)


def _two_project_hub(tmp_path: Path) -> tuple[HubHarness, ProjectId]:
    """A hub whose tenant holds ``default`` and ``beta``, both linked to the fake ``default`` source; returns it
    with ``beta``'s id."""
    hub = build_hub(tmp_path)
    beta = seed_project(hub.engine, "beta")
    link_project(hub.engine, project=beta, source="default")
    return hub, beta


def _ingest(hub: HubHarness, ref: str, project: str) -> str:
    chunk_id = ingest(hub, [{"source": "default", "ref": ref}], project=project)
    hub.clock.advance(timedelta(seconds=1))
    return chunk_id


def _record_usage(hub: HubHarness, chunk_id: str, tokens: int) -> None:
    ChunkUsageStore(hub_store_connections(hub.engine), hub.clock).record_usage(
        chunk_id,
        node_id="nd_build",
        epoch=1,
        runner_id="r1",
        kind="spawn",
        model="claude-opus-4-8",
        input_tokens=tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
        at=hub.clock.now(),
    )


def _push_transcript(hub: HubHarness, chunk_id: str, file_path: str) -> None:
    turns = [_tool_turn(0, "Read", {"file_path": file_path}, timestamp="2026-08-12T09:00:00Z")]
    push = hub.client.post(
        "/api/fleet/transcripts",
        json={"runner_id": "r1", "records": [_record(chunk_id, turns=turns, segment_id=f"sg_{chunk_id}")]},
    )
    assert push.status_code == 200, push.text


def _ndjson(hub: HubHarness, path: str, **params: str) -> list[dict[str, object]]:
    resp = hub.client.get(path, params=params)
    assert resp.status_code == 200, resp.text
    return [json.loads(line) for line in resp.text.splitlines() if line]


def _log(hub: HubHarness, kind: str, *, chunk_id: str | None = None, runner_id: str | None = None) -> None:
    events = hub.services.chunks.events
    assert isinstance(events, ChunkEventsStore), "the feed rows are written through the hub's own events store"
    events.record_event(
        severity=EventLogSeverity.WARNING,
        kind=kind,
        runner_id=runner_id,
        chunk_id=chunk_id,
        lease_id=None,
        node_name=None,
        message=kind,
        detail=None,
        at=hub.clock.now(),
    )
    hub.clock.advance(timedelta(seconds=1))


@pytest.mark.parametrize("path", _ANALYTICS_READS)
def test_every_analytics_read_takes_a_project_and_refuses_an_unknown_one(tmp_path: Path, path: str) -> None:
    hub, _ = _two_project_hub(tmp_path)
    assert hub.client.get(path, params={"project": "beta"}).status_code == 200
    unknown = hub.client.get(path, params={"project": "nope"})
    assert unknown.status_code == 422, unknown.text


def test_spend_reads_and_their_stream_narrow_to_a_projects_chunks(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    _record_usage(hub, d1, 100)
    _record_usage(hub, b1, 7)

    def chunk_keys(**params: str) -> list[str]:
        resp = hub.client.get("/api/analytics/spend/chunks", params=params)
        assert resp.status_code == 200, resp.text
        return sorted(row["chunk_id"] for row in resp.json()["spend"])

    assert chunk_keys() == sorted([d1, b1])
    assert chunk_keys(project="beta") == [b1]
    assert chunk_keys(project=beta) == [b1]
    assert chunk_keys(project="default") == [d1]
    assert [row["chunk_id"] for row in _ndjson(hub, "/api/analytics/spend/chunks/ndjson", project="beta")] == [b1]

    by_node = hub.client.get("/api/analytics/spend/nodes", params={"project": "beta"}).json()["spend"]
    assert [row["input_tokens"] for row in by_node] == [7]
    by_graph = hub.client.get("/api/analytics/spend/graphs").json()["spend"]
    assert sum(row["input_tokens"] for row in by_graph) == 107


def test_event_analytics_and_their_stream_narrow_to_a_projects_chunks(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    _push_transcript(hub, d1, "src/default.py")
    _push_transcript(hub, b1, "src/beta.py")
    hub.hub_services.event_derivation.sweep()

    def event_chunks(**params: str) -> list[str]:
        resp = hub.client.get("/api/analytics/events", params=params)
        assert resp.status_code == 200, resp.text
        return sorted(row["chunk_id"] for row in resp.json()["events"])

    assert event_chunks() == sorted([d1, b1])
    assert event_chunks(project="beta") == [b1]
    assert [row["chunk_id"] for row in _ndjson(hub, "/api/analytics/events/ndjson", project="default")] == [d1]
    files = hub.client.get("/api/analytics/counts/files", params={"project": "beta"}).json()["counts"]
    assert [row["key"] for row in files] == ["src/beta.py"]


def test_the_event_feed_keeps_a_projects_chunks_its_serving_runners_and_rows_naming_neither(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    seed_runner(hub, "r-default", projects=("default",), register=False)
    seed_runner(hub, "r-beta", projects=("beta",), register=False)
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    _log(hub, "default-chunk", chunk_id=d1, runner_id="r-beta")
    _log(hub, "beta-chunk", chunk_id=b1, runner_id="r-default")
    _log(hub, "default-runner", runner_id="r-default")
    _log(hub, "beta-runner", runner_id="r-beta")
    _log(hub, "hub-wide")

    def kinds(**params: str) -> list[str]:
        resp = hub.client.get("/api/events", params=params)
        assert resp.status_code == 200, resp.text
        return [row["kind"] for row in resp.json()["events"]]

    assert kinds() == ["hub-wide", "beta-runner", "default-runner", "beta-chunk", "default-chunk"]
    assert kinds(project="beta") == ["hub-wide", "beta-runner", "beta-chunk"]
    assert kinds(project="default", limit="2") == ["hub-wide", "default-runner"]
    assert hub.client.get("/api/events", params={"project": "nope"}).status_code == 422


def test_the_activity_feed_narrows_each_source_to_the_project(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    seed_runner(hub, "r-default", projects=("default",), register=False)
    seed_runner(hub, "r-beta", projects=("beta",), register=False)
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    _log(hub, "beta-chunk", chunk_id=b1)
    _log(hub, "default-runner", runner_id="r-default")
    _log(hub, "hub-wide")
    registry = hub.services.registry
    assert isinstance(registry, RunnerRegistryStore), "pause facts are written through the hub's own registry"
    registry.record_pause("r-default", paused=True, at=hub.clock.now(), by="op")
    registry.record_pause("r-beta", paused=True, at=hub.clock.now(), by="op")

    def rows(**params: str) -> set[tuple[str, str | None, str | None]]:
        resp = hub.client.get("/api/activity", params=params)
        assert resp.status_code == 200, resp.text
        return {(row["type"], row["chunk_id"] or row["runner_id"], row["kind"]) for row in resp.json()["activity"]}

    whole = rows()
    assert {key for _, key, _ in whole} == {d1, b1, "r-default", "r-beta", None}
    beta_rows = rows(project="beta")
    assert {key for _, key, _ in beta_rows} == {b1, "r-beta", None}
    assert ("event-logged", None, "hub-wide") in beta_rows
    assert ("event-logged", b1, "beta-chunk") in beta_rows
    assert ("chunk-changed", b1, None) in beta_rows
    assert hub.client.get("/api/activity", params={"project": "nope"}).status_code == 422


def test_the_activity_feed_keeps_the_projects_pause_when_another_projects_crowd_the_cap(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    seed_runner(hub, "r-default", projects=("default",), register=False)
    seed_runner(hub, "r-beta", projects=("beta",), register=False)
    registry = hub.services.registry
    assert isinstance(registry, RunnerRegistryStore), "pause facts are written through the hub's own registry"
    registry.record_pause("r-default", paused=True, at=hub.clock.now(), by="op")
    for _ in range(6):
        hub.clock.advance(timedelta(seconds=1))
        registry.record_pause("r-beta", paused=True, at=hub.clock.now(), by="op")

    resp = hub.client.get("/api/activity", params={"project": "default", "limit": "3"})

    assert resp.status_code == 200, resp.text
    assert [row["runner_id"] for row in resp.json()["activity"] if row["type"] == "runner-changed"] == ["r-default"]


def _ask(hub: HubHarness, chunk_id: str, question_id: str, runner_id: str) -> None:
    claim_route(hub, chunk_id, runner_id=runner_id)
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": f"sess-{question_id}",
            "runner_id": runner_id,
            "epoch": 1,
            "question": "Which API?",
            "options": ["rest", "graphql"],
            "asked_at": "2026-08-12T09:00:00+00:00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_a_question_is_labelled_with_its_chunks_project(tmp_path: Path) -> None:
    """``GET /api/questions`` takes no project: each row names its chunk, whose project is the question's, and the
    question's frames carry that project — the label a project-filtered stream and the board select on."""
    hub, beta = _two_project_hub(tmp_path)
    seed_runner(hub, "r-default", projects=("default",))
    seed_runner(hub, "r-beta", projects=("beta",))
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    _ask(hub, d1, "qn_default", "r-default")
    _ask(hub, b1, "qn_beta", "r-beta")
    answered = hub.client.post("/api/questions/qn_beta/answers", json={"answer": "rest", "answered_by": "alice"})
    assert answered.status_code == 201, answered.text

    def project_of(chunk_id: str) -> str:
        return hub.client.get(f"/api/chunks/{chunk_id}").json()["project_slug"]

    listed = hub.client.get("/api/questions")
    assert listed.status_code == 200, listed.text
    assert {row["question_id"]: project_of(row["chunk_id"]) for row in listed.json()} == {"qn_default": "default"}

    default_id = hub.client.get(f"/api/chunks/{d1}").json()["project_id"]
    frames = {
        (event["event"], data["question_id"]): data["project_id"]
        for event in emitted_events(hub)
        if event["event"] in {"question-asked", "question-answered"}
        for data in [json.loads(event["data"])]
    }
    assert frames == {
        ("question-asked", "qn_default"): default_id,
        ("question-asked", "qn_beta"): beta,
        ("question-answered", "qn_beta"): beta,
    }
