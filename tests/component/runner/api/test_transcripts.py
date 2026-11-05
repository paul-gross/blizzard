"""The transcript route — ``GET /api/leases/{lease_id}/transcript``.

Exercised over a real store via ``TestClient``, with fake ``IReadTranscriptRepository``/
``IReadArchivedTranscriptRepository`` seams standing in for the filesystem and the hub — this
file's job is the route's status-code contract, not normalization or transport. The
full resolution table is pinned at the service tier (``tests/unit/runner/transcripts/test_service.py``)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Unpack

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.leases.model import NewLease
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.repository import Transcript, Turn
from tests.support.config_inputs import create_runner_app
from tests.support.factory_fields import NewLeaseFields
from tests.support.runner_client import runner_client
from tests.support.runner_fakes import (
    FakeArchivedTranscriptRepository,
    SqlAlchemyRunnerStore,
)
from tests.support.scaffolding import assert_all_timestamps_utc
from tests.support.transcripts_api import FakeTranscriptRepository, app_with_transcripts

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _seed_lease(store: SqlAlchemyRunnerStore, **overrides: Unpack[NewLeaseFields]) -> None:
    fields: NewLeaseFields = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "graph_id": "gr_1",
        "node_id": "nd_build",
        "node_name": "build",
        "epoch": 1,
        "retries_max": 2,
        "created_at": _NOW,
    }
    fields.update(overrides)
    store.record_lease(NewLease(**fields))


def test_200_with_turns_for_an_active_lease(tmp_path: Path) -> None:
    turn = Turn(
        index=0,
        kind="env",
        timestamp=_NOW,
        text="build the thing",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )
    transcript = Transcript(session_id="sess-a", available=True, reason=None, turns=[turn], truncated=False)
    repo = FakeTranscriptRepository({"sess-a": transcript})
    app, store, _repo = app_with_transcripts(tmp_path, repo=repo)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )

    with runner_client(app) as client:
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
        "tool": None,
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }
    # An open lease's response always carries a provenance of "local" and a
    # dropped-turn count of zero, in addition to the turns pinned above.
    assert body["provenance"] == "local"
    assert body["hub_unreachable"] is False
    assert_all_timestamps_utc(body)


def test_unknown_recorded_owner_returns_503_without_reading_default_transcript_source(tmp_path: Path) -> None:
    app, store, repo = app_with_transcripts(tmp_path, harnesses=HarnessRegistry({}))
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    with store.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE leases SET harness_id = 'missing' WHERE lease_id = 'lease_1'")

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 503
    assert "unknown coding harness 'missing'" in resp.json()["detail"]
    assert repo.calls == []


def test_200_spawning_when_no_session_id_yet(tmp_path: Path) -> None:
    app, store, repo = app_with_transcripts(tmp_path)
    _seed_lease(store)
    # No record_spawn — session_id stays unset.

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "spawning"
    assert body["session_id"] is None
    assert body["turns"] == []
    assert repo.calls == []  # never reaches the repository — no session to look up


def test_200_not_found_when_the_file_is_missing(tmp_path: Path) -> None:
    app, store, _repo = app_with_transcripts(tmp_path)  # empty repo -> not_found for any session
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "not_found"


def test_200_for_a_closed_lease_with_no_hub_segments_falls_back_to_local(tmp_path: Path) -> None:
    """A closed lease's transcript stays reachable — the route must use the closure-spanning
    ``lease()``, not ``active_lease()``. The hub is asked first; an unscripted
    ``FakeArchivedTranscriptRepository`` answers "holds nothing", pinning the fall-back."""
    transcript = Transcript(session_id="sess-a", available=True, reason=None, turns=[], truncated=False)
    repo = FakeTranscriptRepository({"sess-a": transcript})
    archived = FakeArchivedTranscriptRepository()
    app, store, _repo = app_with_transcripts(tmp_path, repo=repo, archived=archived)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)
    assert store.active_lease("lease_1") is None  # the cliff `active_lease()` would hit

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["session_id"] == "sess-a"
    assert body["provenance"] == "local"
    assert body["hub_unreachable"] is False
    assert archived.calls == [("ch_1", "nd_build", 1)]  # the hub was asked first
    # A closed lease's bindings are always released — the hint passed to the
    # repository is legitimately None; the glob-by-session-id lookup does not need it.
    assert _repo.calls == [("sess-a", None)]


def test_200_for_a_closed_lease_with_hub_segments_serves_them(tmp_path: Path) -> None:
    """The hub's segments win once found — the local fake is never consulted."""
    hub_turn = Turn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text="from the hub",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )
    archived = FakeArchivedTranscriptRepository(
        {("ch_1", "nd_build", 1): ArchivedTranscript(status="found", turns=[hub_turn], truncated=False)}
    )
    repo = FakeTranscriptRepository()
    app, store, _repo = app_with_transcripts(tmp_path, repo=repo, archived=archived)
    _seed_lease(store)
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_closure(lease_id="lease_1", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW)

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/transcript")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["available"] is True
    assert body["provenance"] == "archived"
    assert body["hub_unreachable"] is False
    assert [t["text"] for t in body["turns"]] == ["from the hub"]
    assert repo.calls == []  # local is never consulted once the hub answers


def test_404_for_a_lease_that_never_existed(tmp_path: Path) -> None:
    app, _store, _repo = app_with_transcripts(tmp_path)

    with runner_client(app) as client:
        resp = client.get("/api/leases/no-such-lease/transcript")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "no lease no-such-lease"


def test_503_when_transcript_service_unwired(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_runner_app(config)) as client:
        resp = client.get("/api/leases/lease_1/transcript")
    assert resp.status_code == 503
