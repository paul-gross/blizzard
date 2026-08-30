"""The runner-plane, chunk-scoped transcript segment routes — ``GET /api/chunks/{chunk_id}/transcripts``
and ``GET .../transcripts/{segment_id}`` (runner-node-grouped-transcripts, D1/D3/D4). Exercised over a
real store via ``TestClient``, with a fake ``IReadTranscriptRepository`` standing in for the filesystem
and an ``IReadArchivedTranscriptRepository`` that raises on any call — a route reaching the hub at all
fails the test outright, proving D1's local-only claim rather than merely asserting an empty call log
afterward."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.store.repository import NewLease
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.repository import Transcript, Turn
from blizzard.runner.transcripts.service import TranscriptService
from tests.runner_fakes import make_store
from tests.support import assert_all_timestamps_utc

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


class FakeTranscriptRepository:
    """An in-process ``IReadTranscriptRepository`` — one canned ``Transcript`` per session id."""

    def __init__(self, by_session_id: dict[str, Transcript] | None = None) -> None:
        self._by_session_id = by_session_id or {}

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        if session_id in self._by_session_id:
            return self._by_session_id[session_id]
        return Transcript(session_id=session_id, available=False, reason="not_found", turns=[], truncated=False)


class RaisingArchivedTranscriptRepository:
    """Stands in for the hub — any call fails the test immediately (D1)."""

    def read_turns(self, *, chunk_id: str, node_id: str, epoch: int) -> ArchivedTranscript:
        raise AssertionError("the runner-plane segment routes must never call the hub")


def _app_with_segments(tmp_path: Path, *, repo: FakeTranscriptRepository | None = None):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    service = TranscriptService(
        store=store,
        transcripts=repo or FakeTranscriptRepository(),
        archived=RaisingArchivedTranscriptRepository(),
        workspace_root="",
    )
    return create_app(config, runner_store=store, transcripts=service), store


def _mint(store, *, chunk="ch_1", node="nd_build", epoch=1, lease="lease_1", runner_id="r1"):  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id=node,
            node_name="build",
            epoch=epoch,
            runner_id=runner_id,
            retries_max=2,
            created_at=_NOW,
        )
    )


@pytest.mark.component
def test_index_returns_the_chunks_segments(tmp_path: Path) -> None:
    app, store = _app_with_segments(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)

    resp = TestClient(app).get("/api/chunks/ch_1/transcripts")

    assert resp.status_code == 200
    body = resp.json()
    assert body["chunk_id"] == "ch_1"
    [entry] = body["segments"]
    assert (entry["node_id"], entry["epoch"], entry["spawn_generation"]) == ("nd_build", 1, 1)
    assert (entry["turn_range_start"], entry["turn_range_end"]) == (0, 0)
    assert entry["final"] is False
    assert_all_timestamps_utc(body)


@pytest.mark.component
def test_index_is_empty_for_a_chunk_this_runner_never_held(tmp_path: Path) -> None:
    app, store = _app_with_segments(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)

    resp = TestClient(app).get("/api/chunks/ch_other/transcripts")

    assert resp.status_code == 200
    assert resp.json() == {"chunk_id": "ch_other", "segments": []}


@pytest.mark.component
def test_index_includes_every_segment_under_the_chunk_regardless_of_which_runner_id_its_lease_names(
    tmp_path: Path,
) -> None:
    """Ownership-exclusion (D3): a chunk whose leases carry two DIFFERENT ``runner_id``s —
    unreachable in real fleet data, constructed only to prove the point — still reads back
    every segment this store holds; confinement is the physical store, never a filter on ``runner_id``."""
    app, store = _app_with_segments(tmp_path)
    _mint(store, lease="lease_1", node="nd_build", epoch=1, runner_id="r1")
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    _mint(store, lease="lease_2", node="nd_verify", epoch=1, runner_id="r2")
    store.record_spawn("lease_2", pid=2, process_start_time="2", session_id="sess-b", spawned_at=_NOW)

    resp = TestClient(app).get("/api/chunks/ch_1/transcripts")

    assert resp.status_code == 200
    node_ids = {entry["node_id"] for entry in resp.json()["segments"]}
    assert node_ids == {"nd_build", "nd_verify"}


@pytest.mark.component
def test_content_returns_turns_for_a_known_segment(tmp_path: Path) -> None:
    turn = Turn(
        index=0,
        kind="env",
        timestamp=_NOW,
        text="hello",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )
    repo = FakeTranscriptRepository(
        {"sess-a": Transcript(session_id="sess-a", available=True, reason=None, turns=[turn], truncated=False)}
    )
    app, store = _app_with_segments(tmp_path, repo=repo)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    [segment] = store.transcript_segments_for_chunk("ch_1")

    resp = TestClient(app).get(f"/api/chunks/ch_1/transcripts/{segment.segment_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["segment_id"] == segment.segment_id
    assert body["truncated"] is False
    [turn_body] = body["turns"]
    assert turn_body["text"] == "hello"


@pytest.mark.component
def test_content_reports_unavailability_rather_than_404_when_the_session_file_is_gone(tmp_path: Path) -> None:
    app, store = _app_with_segments(tmp_path)  # no fake entry for "sess-a" -> not_found
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    [segment] = store.transcript_segments_for_chunk("ch_1")

    resp = TestClient(app).get(f"/api/chunks/ch_1/transcripts/{segment.segment_id}")

    assert resp.status_code == 200
    body = resp.json()
    assert body["truncated"] is True
    assert body["turns"] == []


@pytest.mark.component
def test_content_404s_for_an_unknown_segment_id(tmp_path: Path) -> None:
    app, _store = _app_with_segments(tmp_path)

    resp = TestClient(app).get("/api/chunks/ch_1/transcripts/seg_nope")

    assert resp.status_code == 404


@pytest.mark.component
def test_content_404s_when_the_segment_belongs_to_a_different_chunk_in_the_url(tmp_path: Path) -> None:
    app, store = _app_with_segments(tmp_path)
    _mint(store)
    store.record_spawn("lease_1", pid=1, process_start_time="1", session_id="sess-a", spawned_at=_NOW)
    [segment] = store.transcript_segments_for_chunk("ch_1")

    resp = TestClient(app).get(f"/api/chunks/ch_other/transcripts/{segment.segment_id}")

    assert resp.status_code == 404
