"""Phase 2 of the derive-once read sweep (component tier): a single-chunk write verb's
domain gate reuses `ChunkChanged.before`'s own facts load instead of reloading them, and
`answer_question` stops re-reading the question row it already has once it wins the CAS.
Identity resolves through one `Depends(require(...))` now, not a second manual resolve."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest

from blizzard.auth_core import Role
from blizzard.foundation.clock import IClock
from blizzard.hub.auth.models import Session
from blizzard.hub.auth.sessions import IReadSessionRepository
from blizzard.hub.domain.work import ChunkFacts
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from tests.support import HubHarness, build_hub, hub_store_connections, ingest, seed_session, seed_user
from tests.test_decisions_api import _GATE_YAML
from tests.test_questions_api import _asked

pytestmark = pytest.mark.component


class _CountingFactsStore(ChunkFactsStore):
    """Counts ``load_facts`` per chunk id — the seam a write verb's route
    (``ChunkChanged.before``/``publish``) and its domain gate both reach through."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.load_facts_calls: dict[str, int] = {}

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls[chunk_id] = self.load_facts_calls.get(chunk_id, 0) + 1
        return super().load_facts(chunk_id)


def _wire_counting_facts(hub: HubHarness) -> _CountingFactsStore:
    counting = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, chunks=replace(hub.services.chunks, facts=counting))
    return counting


@dataclass
class _CountingQuestions:
    """Forwards every call to the hub's real questions store, counting only
    ``get_question`` — the read ``answer_question`` used to issue twice per request."""

    inner: Any
    get_question_calls: int = 0

    def get_question(self, question_id: str) -> Any:
        self.get_question_calls += 1
        return self.inner.get_question(question_id)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)


def _wire_counting_questions(hub: HubHarness) -> _CountingQuestions:
    counting = _CountingQuestions(hub.services.chunks.questions)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, chunks=replace(hub.services.chunks, questions=counting))
    return counting


@dataclass
class _CountingSessions:
    """Forwards ``get_by_hash`` to the hub's real session repository, counting calls —
    the identity-resolution seam a session-gated route reaches through."""

    inner: IReadSessionRepository
    get_by_hash_calls: int = 0

    def get_by_hash(self, id_hash: str) -> Session | None:
        self.get_by_hash_calls += 1
        return self.inner.get_by_hash(id_hash)


def _wire_counting_sessions(hub: HubHarness) -> _CountingSessions:
    counting = _CountingSessions(hub.services.sessions)
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, sessions=counting)
    return counting


# --- facts-load counting: pause, stop, question-answer -----------------------------


def test_pause_calls_load_facts_exactly_twice(tmp_path: Path) -> None:
    """Before: 4 (``ChunkChanged.before``, ``PauseService``'s own reload, ``publish``,
    ``ChunkView.of``'s reload). After: 2 — pre-write and post-write only."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    counting = _wire_counting_facts(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/pause", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    assert counting.load_facts_calls[chunk_id] == 2


def test_stop_calls_load_facts_exactly_twice(tmp_path: Path) -> None:
    """Before: 4 (``ChunkChanged.before``, ``StopService``'s own reload, ``publish``,
    ``ChunkView.of``'s reload). After: 2 — pre-write and post-write only."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    counting = _wire_counting_facts(hub)

    resp = hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "alice"})

    assert resp.status_code == 202, resp.text
    assert counting.load_facts_calls[chunk_id] == 2


def test_answer_question_calls_load_facts_exactly_twice(tmp_path: Path) -> None:
    """Already 2 before and after (``QuestionService.answer`` never touches facts) —
    pre-write via ``ChunkChanged.before``, post-write via ``publish``; unchanged by this
    phase, which instead drops the extra ``get_question`` read below."""
    hub = build_hub(tmp_path)
    chunk_id = _asked(hub)
    counting = _wire_counting_facts(hub)

    resp = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})

    assert resp.status_code == 201, resp.text
    assert counting.load_facts_calls[chunk_id] == 2


def test_answer_question_calls_get_question_exactly_once(tmp_path: Path) -> None:
    """Before: 2 (the pre-answer read, then re-reading the just-written winner to find
    its ``chunk_id``). After: 1 — the pre-answer read already names that chunk."""
    hub = build_hub(tmp_path)
    _asked(hub)
    counting = _wire_counting_questions(hub)

    resp = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})

    assert resp.status_code == 201, resp.text
    assert counting.get_question_calls == 1


# --- identity resolves once per request, under auth.mode = "oauth" -----------------


def _superuser_cookie(hub: HubHarness) -> dict[str, str]:
    user = seed_user(hub, username="root", role=Role.SUPERUSER)
    token = seed_session(hub, user)
    return {"Cookie": f"bz_session={token}"}


def test_answer_question_resolves_the_session_exactly_once_under_oauth(tmp_path: Path) -> None:
    """Before: 2 (``require(QUESTION_ANSWER)``'s own resolve, plus ``resolved_username``'s
    separate manual resolve). After: 1 — a single ``Depends(require(...))`` parameter."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    cookie = _superuser_cookie(hub)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML}, headers=cookie)
    assert graph.status_code == 201, graph.text
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}, headers=cookie).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote", headers=cookie).status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    asked = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_1",
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which API?",
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
        headers=cookie,
    )
    assert asked.status_code == 201, asked.text
    counting = _wire_counting_sessions(hub)

    resp = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"}, headers=cookie)

    assert resp.status_code == 201, resp.text
    assert counting.get_by_hash_calls == 1


def test_resolve_decision_resolves_the_session_exactly_once_under_oauth(tmp_path: Path) -> None:
    """Before: 2 (``require(GATE_RESOLVE)``'s own resolve, plus ``resolved_username``'s
    separate manual resolve). After: 1 — a single ``Depends(require(...))`` parameter."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    cookie = _superuser_cookie(hub)
    decision_id = _open_decision_under_oauth(hub, cookie)
    counting = _wire_counting_sessions(hub)

    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}, headers=cookie)

    assert resp.status_code == 200, resp.text
    assert counting.get_by_hash_calls == 1


def _open_decision_under_oauth(hub: HubHarness, cookie: dict[str, str]) -> str:
    """``_open_decision``'s own setup, cookie-carrying on every session-gated route —
    the claim/lease/completion trio stays bare, since those sit on the runner surface."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GATE_YAML}, headers=cookie)
    assert graph.status_code == 201, graph.text
    build_node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")
    chunk_id = hub.client.post("/api/chunks", json={"tokens": ["default:2"]}, headers=cookie).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote", headers=cookie).status_code == 202
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    lease = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_id, "epoch": 1}}],
        },
    )
    assert lease.status_code == 200, lease.text
    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": build_node_id, "artifacts": []},
    )
    assert resp.json()["outcome"] == "parked_at_gate", resp.text
    decision = hub.client.get(f"/api/chunks/{chunk_id}", headers=cookie).json()["decision"]
    assert decision is not None
    return str(decision["decision_id"])
