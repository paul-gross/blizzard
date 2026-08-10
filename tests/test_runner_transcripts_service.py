"""``transcripts/service.py`` — home selection for a lease's transcript (blizzard#249, D1).

Every branch of Decision 1's resolution table, driven against a real store (for
``lease``/``active_lease``) with fake local and archived repositories standing in for the
filesystem and the hub — so this file's job is the *resolution*, never the transport or
the normalization, both pinned elsewhere."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.runner.store.repository import NewLease
from blizzard.runner.transcripts.archived_repository import ArchivedTranscript
from blizzard.runner.transcripts.repository import Transcript, Turn
from blizzard.runner.transcripts.service import TranscriptService
from tests.runner_fakes import FakeArchivedTranscriptRepository, make_store

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)
_KEY = ("ch_1", "nd_build", 1)


class FakeTranscriptRepository:
    """An in-process ``IReadTranscriptRepository`` — one canned ``Transcript`` per session id."""

    def __init__(self, by_session_id: dict[str, Transcript] | None = None) -> None:
        self._by_session_id = by_session_id or {}
        self.calls: list[str] = []

    def read_turns(self, session_id: str, *, spawn_cwd: str | None) -> Transcript:
        self.calls.append(session_id)
        if session_id in self._by_session_id:
            return self._by_session_id[session_id]
        return Transcript(session_id=session_id, available=False, reason="not_found", turns=[], truncated=False)


def _service(
    store, *, local: FakeTranscriptRepository | None = None, archived: FakeArchivedTranscriptRepository | None = None
):  # type: ignore[no-untyped-def]
    return TranscriptService(
        store=store,
        transcripts=local or FakeTranscriptRepository(),
        archived=archived or FakeArchivedTranscriptRepository(),
        workspace_root="",
    )


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


def _local_turn(text: str) -> Turn:
    return Turn(
        index=0,
        kind="env",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


def _hub_turn(text: str) -> Turn:
    return Turn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text=text,
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )


@pytest.mark.unit
def test_no_lease_ever_existed_resolves_to_none(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    assert _service(store).for_lease("no-such-lease") is None


@pytest.mark.unit
def test_a_lease_with_no_session_yet_is_spawning_local_and_never_asks_the_hub(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)  # no record_spawn — session_id stays unset
    archived = FakeArchivedTranscriptRepository()

    resolved = _service(store, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.transcript.available is False
    assert resolved.transcript.reason == "spawning"
    assert resolved.provenance == "local"
    assert resolved.hub_unreachable is False
    assert archived.calls == []


@pytest.mark.unit
def test_an_open_lease_reads_local_and_is_never_asked_of_the_hub(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a", available=True, reason=None, turns=[_local_turn("hi")], truncated=False
            )
        }
    )
    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="found", turns=[_hub_turn("should never be seen")], truncated=False)}
    )

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert [t.text for t in resolved.transcript.turns] == ["hi"]
    assert resolved.hub_unreachable is False
    assert archived.calls == []  # D1: an open lease is never asked of the hub at all


def _close(store, **overrides: object) -> None:  # type: ignore[no-untyped-def]
    fields: dict[str, object] = {
        "lease_id": "lease_1",
        "chunk_id": "ch_1",
        "node_id": "nd_build",
        "reason": "transitioned",
        "closed_at": _NOW,
    }
    fields.update(overrides)
    store.record_closure(**fields)  # type: ignore[arg-type]


def _closed_lease(store) -> None:  # type: ignore[no-untyped-def]
    _seed_lease(store)
    store.record_spawn("lease_1", pid=100, process_start_time="start-100", session_id="sess-a", spawned_at=_NOW)
    _close(store)
    assert store.active_lease("lease_1") is None


def _buffer_unshipped_turn(store) -> None:  # type: ignore[no-untyped-def]
    """One content row left unacked in the transcript lane's outbound buffer — what a
    closed lease looks like while the bounded drain is still catching up."""
    segment = store.open_transcript_segments()
    segment_id = segment[0].segment_id if segment else _segment_id(store)
    store.record_transcript_deltas(
        segment_id=segment_id,
        chunk_id="ch_1",
        cursor="1",
        shipped_bytes=10,
        shipped_turns=1,
        normalizer_version="v1",
        harness_version="claude-code-1.0",
        payloads=['{"segment_id": "sg", "turns": []}'],
        created_at=_NOW,
    )


def _segment_id(store) -> str:  # type: ignore[no-untyped-def]
    [delta] = store.pending_transcript_outbound()
    return delta.segment_id


@pytest.mark.unit
def test_a_closed_fully_acked_lease_serves_the_hubs_segments(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a",
                available=True,
                reason=None,
                turns=[_local_turn("should never be seen")],
                truncated=False,
            )
        }
    )
    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="found", turns=[_hub_turn("from the hub")], truncated=False)}
    )

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "archived"
    assert resolved.transcript.available is True
    assert [t.text for t in resolved.transcript.turns] == ["from the hub"]
    assert resolved.hub_unreachable is False
    assert local.calls == []  # local is never consulted once the hub answers


@pytest.mark.unit
def test_a_closed_lease_with_unshipped_turns_still_reads_local_not_the_hubs_prefix(tmp_path: Path) -> None:
    """Issue #249 AC1 — *local until acked, hub after*. A bounded drain leaves a just-closed
    lease's tail buffered, so the hub holds a **prefix**; serving that under the archived
    badge would silently shorten the transcript."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    _buffer_unshipped_turn(store)
    assert store.has_unshipped_transcript_content("ch_1") is True
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a",
                available=True,
                reason=None,
                turns=[_local_turn("the whole file"), _local_turn("including the tail")],
                truncated=False,
            )
        }
    )
    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="found", turns=[_hub_turn("only the prefix")], truncated=False)}
    )

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert [t.text for t in resolved.transcript.turns] == ["the whole file", "including the tail"]
    assert archived.calls == []  # not even asked: the answer cannot be the hub's


@pytest.mark.unit
def test_a_pending_final_marker_alone_does_not_hold_a_lease_local(tmp_path: Path) -> None:
    """A closed lease always leaves its segment's final marker unacked for a tick or two,
    and that marker carries no turns — treating it as unshipped content would strand every
    lease on the local file and make the hub read unreachable in practice."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    assert [d.final for d in store.pending_transcript_outbound()] == [True]  # the marker, and only it

    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="found", turns=[_hub_turn("from the hub")], truncated=False)}
    )

    resolved = _service(store, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "archived"
    assert [t.text for t in resolved.transcript.turns] == ["from the hub"]


@pytest.mark.unit
def test_a_closed_lease_with_no_hub_segments_falls_back_to_local(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a", available=True, reason=None, turns=[_local_turn("from local")], truncated=False
            )
        }
    )
    archived = FakeArchivedTranscriptRepository()  # unscripted key -> "empty"

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert [t.text for t in resolved.transcript.turns] == ["from local"]
    assert resolved.hub_unreachable is False


@pytest.mark.unit
def test_a_closed_lease_the_hub_holds_only_cap_rejected_records_falls_back_to_local(tmp_path: Path) -> None:
    """The hub answered, but holds nothing renderable — that empty "found" must not win
    unconditionally over an available local transcript."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a", available=True, reason=None, turns=[_local_turn("from local")], truncated=False
            )
        }
    )
    archived = FakeArchivedTranscriptRepository({_KEY: ArchivedTranscript(status="found", turns=[], truncated=True)})

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert [t.text for t in resolved.transcript.turns] == ["from local"]
    assert resolved.hub_unreachable is False


@pytest.mark.unit
def test_a_closed_lease_the_hub_refuses_falls_back_to_local(tmp_path: Path) -> None:
    """A refusal is a definite answer, not a transport failure (D1) — resolved to local
    exactly like "holds nothing", never the hub-unreachable state."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a", available=True, reason=None, turns=[_local_turn("from local")], truncated=False
            )
        }
    )
    archived = FakeArchivedTranscriptRepository({_KEY: ArchivedTranscript(status="refused", turns=[], truncated=False)})

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert resolved.hub_unreachable is False


@pytest.mark.unit
def test_a_closed_lease_the_hub_is_unreachable_but_local_still_answers_falls_back_quietly(tmp_path: Path) -> None:
    """D1's one non-obvious cell: the hub is unreachable, but local can still answer, so
    the wire's ``hub_unreachable`` flag stays unset — the closed-lease view degrades to
    local without flagging an outage the operator has no local evidence of."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)
    local = FakeTranscriptRepository(
        {
            "sess-a": Transcript(
                session_id="sess-a", available=True, reason=None, turns=[_local_turn("from local")], truncated=False
            )
        }
    )
    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="unreachable", turns=[], truncated=False)}
    )

    resolved = _service(store, local=local, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert [t.text for t in resolved.transcript.turns] == ["from local"]
    assert resolved.hub_unreachable is False


@pytest.mark.unit
def test_a_closed_lease_the_hub_is_unreachable_and_local_cannot_answer_either_flags_it(tmp_path: Path) -> None:
    """D1's remaining cell: the hub is unreachable *and* local cannot answer either — the
    only case the wire's ``hub_unreachable`` flag is set."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _closed_lease(store)  # no local fake entry for "sess-a" -> not_found
    archived = FakeArchivedTranscriptRepository(
        {_KEY: ArchivedTranscript(status="unreachable", turns=[], truncated=False)}
    )

    resolved = _service(store, archived=archived).for_lease("lease_1")

    assert resolved is not None
    assert resolved.provenance == "local"
    assert resolved.transcript.available is False
    assert resolved.transcript.reason == "not_found"
    assert resolved.hub_unreachable is True
