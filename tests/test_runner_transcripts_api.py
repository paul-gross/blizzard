"""The transcript route — ``GET /api/leases/{lease_id}/transcript`` (issue #29, blizzard#249).

Exercised over a real store via ``TestClient``: a real sqlite store for lease facts, and
fake ``IReadTranscriptRepository``/``IReadArchivedTranscriptRepository`` seams standing in
for the filesystem and the hub, so this file's job is the route's resolution and
status-code contract, not the normalization or the transport. Decision 1's full
resolution table is pinned at the service tier (``test_runner_transcripts_service.py``);
this file only needs enough of it to prove the wire carries what the service resolves."""

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
from tests.runner_fakes import FakeArchivedTranscriptRepository, make_store
from tests.support import assert_all_timestamps_utc

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


class FakeTranscriptRepository:
    """An in-process ``IReadTranscriptRepository`` — one canned ``Transcript`` per session id.

    ``spawn_cwd`` is recorded but not consulted; this fake exists to control what the
    route sees, not to re-derive the disambiguation hint."""

    def __init__(self, by_session_id: dict[str, Transcript] | None = None) -> None:
        self._by_session_id = by_session_id or {}
        self.calls: list[tuple[str, str | None]] = []

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        self.calls.append((session_id, spawn_cwd))
        if session_id in self._by_session_id:
            return self._by_session_id[session_id]
        return Transcript(session_id=session_id, available=False, reason="not_found", turns=[], truncated=False)


def _app_with_transcripts(
    tmp_path: Path,
    *,
    repo: FakeTranscriptRepository | None = None,
    archived: FakeArchivedTranscriptRepository | None = None,
    workspace_root: str = "",
):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    repo = repo or FakeTranscriptRepository()
    archived = archived or FakeArchivedTranscriptRepository()
    service = TranscriptService(store=store, transcripts=repo, archived=archived, workspace_root=workspace_root)
    return create_app(config, runner_store=store, transcripts=service), store, repo


def _seed_lease(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "runner_id": "r1",
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))  # type: ignore[arg-type]


@pytest.mark.component
def test_200_with_turns_for_an_active_lease(tmp_path: Path) -> None:
    turn = Turn(
        index=0,
        kind="env",
        timestamp=_NOW,
        text="build the thing",
        tool_name=None,
        tool_input=None,
        tool_output=None,
        truncated=False,
    )
    transcript = Transcript(session_id="sess-a", available=True, reason=None, turns=[turn], truncated=False)
    repo = FakeTranscriptRepository({"sess-a": transcript})
    app, store, _repo = _app_with_transcripts(tmp_path, repo=repo)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lease_id"] == "lease_1"
    assert body["session_id"] == "sess-a"
    assert body["available"] is True
    assert body["reason"] is None
    assert body["truncated"] is False
    assert len(body["turns"]) == 1
    assert body["turns"][0] == {
        "index": 0,
        "kind": "env",
        "timestamp": _NOW.isoformat(),
        "text": "build the thing",
        "tool_name": None,
        "tool_input": None,
        "tool_output": None,
        "truncated": False,
    }
    # An open lease's response is not byte-identical with `master`: it gains a
    # provenance of "local" and a dropped-turn count of zero, and nothing else.
    assert body["provenance"] == "local"
    assert body["hub_unreachable"] is False
    assert body["dropped_turns"] == 0
    assert_all_timestamps_utc(body)


@pytest.mark.component
def test_200_spawning_when_no_session_id_yet(tmp_path: Path) -> None:
    app, store, repo = _app_with_transcripts(tmp_path)
    _seed_lease(store)
    # No record_spawn — session_id stays unset.

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "spawning"
    assert body["session_id"] is None
    assert body["turns"] == []
    assert repo.calls == []  # never reaches the repository — no session to look up


@pytest.mark.component
def test_200_not_found_when_the_file_is_missing(tmp_path: Path) -> None:
    app, store, _repo = _app_with_transcripts(tmp_path)  # empty repo -> not_found for any session
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "not_found"


@pytest.mark.component
def test_200_for_a_closed_lease_with_no_hub_segments_falls_back_to_local(tmp_path: Path) -> None:
    """A closed lease's transcript stays reachable —
    ``active_lease()`` would 404 here; the route must use the closure-spanning ``lease()``.
    The hub is asked first (D1); an unscripted ``FakeArchivedTranscriptRepository`` answers
    "holds nothing", so this pins the fall-back-to-local branch of Decision 1's table."""
    transcript = Transcript(session_id="sess-a", available=True, reason=None, turns=[], truncated=False)
    repo = FakeTranscriptRepository({"sess-a": transcript})
    archived = FakeArchivedTranscriptRepository()
    app, store, _repo = _app_with_transcripts(tmp_path, repo=repo, archived=archived)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.active_lease("lease_1") is None  # the cliff `active_lease()` would hit

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["session_id"] == "sess-a"
    assert body["provenance"] == "local"
    assert body["hub_unreachable"] is False
    assert body["dropped_turns"] == 0
    assert archived.calls == [("ch_1", "nd_build", 1)]  # the hub was asked first
    # A closed lease's bindings are always released — the hint passed to the
    # repository is legitimately None; the glob-by-session-id lookup does not need it.
    assert _repo.calls == [("sess-a", None)]


@pytest.mark.component
def test_200_for_a_closed_lease_with_hub_segments_serves_them(tmp_path: Path) -> None:
    """The hub's segments win once found (D1) — the local fake is never consulted."""
    hub_turn = Turn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text="from the hub",
        tool_name=None,
        tool_input=None,
        tool_output=None,
        truncated=False,
    )
    archived = FakeArchivedTranscriptRepository(
        {("ch_1", "nd_build", 1): ArchivedTranscript(status="found", turns=[hub_turn], truncated=False, dropped=2)}
    )
    repo = FakeTranscriptRepository()
    app, store, _repo = _app_with_transcripts(tmp_path, repo=repo, archived=archived)
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    with TestClient(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["provenance"] == "archived"
    assert body["hub_unreachable"] is False
    assert body["dropped_turns"] == 2
    assert [t["text"] for t in body["turns"]] == ["from the hub"]
    assert repo.calls == []  # local is never consulted once the hub answers


@pytest.mark.component
def test_404_for_a_lease_that_never_existed(tmp_path: Path) -> None:
    app, _store, _repo = _app_with_transcripts(tmp_path)

    with TestClient(app) as client:
        resp = client.get("/api/leases/no-such-lease/transcript")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "no lease no-such-lease"


@pytest.mark.component
def test_503_when_transcript_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config)) as client:
        resp = client.get("/api/leases/lease_1/transcript")
    assert resp.status_code == 503
