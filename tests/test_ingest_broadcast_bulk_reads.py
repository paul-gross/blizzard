"""``POST /api/fleet/events``' broadcast side batched across a landed fact batch's
distinct chunks (component tier) — ``IngestBroadcast.before_ingest``/``publish`` each read
every touched chunk's state once through :class:`~blizzard.hub.api.chunk_events.ChunkChanged`'s
and :class:`~blizzard.hub.api.chunk_events.ChunkFrameState`'s plural constructors, rather
than once per fact. Also pins that the frames this path emits are unchanged, and that the
non-batch, single-chunk ``ChunkChanged`` call sites keep their own statement counts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.hub.api.ingest_broadcast import IngestBroadcast
from blizzard.hub.events.broker import CHUNK_CHANGED
from blizzard.hub.events.broker import QUESTION_ASKED as QUESTION_ASKED_EVENT
from blizzard.wire.facts import RunnerFact, RunnerFactBatch
from tests.support import build_hub, count_queries, emitted_events, ingest, report_lease

pytestmark = pytest.mark.component

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Complete.
          to: done
"""


def _claim(hub, ref: str, *, runner_id: str) -> str:  # type: ignore[no-untyped-def]
    """A promoted, claimed chunk — a live route under its own ``runner_id`` so a batch's
    ``lease.minted``/``escalation.recorded`` facts have somewhere real to land."""
    chunk_id = ingest(hub, [{"source": "default", "ref": ref}])
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": f"w-{runner_id}", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def _seed_scale(hub, m: int) -> list[str]:  # type: ignore[no-untyped-def]
    """``m`` distinct claimed chunks, each under its own runner id."""
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    return [_claim(hub, str(i), runner_id=f"r{i}") for i in range(m)]


def _scaled_batch(chunk_ids: list[str]) -> RunnerFactBatch:
    """Two facts per chunk (a lease re-mint and an escalation) — ``k = 2 * len(chunk_ids)``
    facts spread across ``len(chunk_ids)`` distinct chunks, under one fresh runner id
    unrelated to any chunk's own claim (the route-token check runs in ``warn`` mode, so a
    submission from a different runner than the live route's holder is only logged, never
    rejected — this batch's own reporting identity is irrelevant to what it counts)."""
    facts: list[RunnerFact] = []
    seq = 1
    for chunk_id in chunk_ids:
        facts.append(RunnerFact(seq=seq, kind="lease.minted", payload={"chunk_id": chunk_id, "epoch": 2}))
        seq += 1
        facts.append(
            RunnerFact(
                seq=seq,
                kind="escalation.recorded",
                payload={"chunk_id": chunk_id, "epoch": 1, "takeover_command": "cd wd && x"},
            )
        )
        seq += 1
    return RunnerFactBatch(runner_id="counter", facts=facts)


def test_before_ingest_and_publish_query_counts_are_independent_of_batch_size(tmp_path: Path) -> None:
    """``before_ingest`` and ``publish`` each issue the same statement count whether the
    batch touches 2 chunks (4 facts) or 6 (12 facts, 3x) — both well within one
    ``id_batches`` batch, so the counts are exactly equal, not merely bounded."""
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    small_ids = _seed_scale(small, 2)
    large = build_hub(tmp_path / "large")
    large_ids = _seed_scale(large, 6)

    holder: dict[str, object] = {}

    def before(hub, ids: list[str], key: str) -> None:  # type: ignore[no-untyped-def]
        batch = _scaled_batch(ids)
        holder[f"{key}_batch"] = batch
        holder[f"{key}_broadcast"] = IngestBroadcast.before_ingest(hub.services, batch)

    small_before_count = count_queries(small.engine, lambda: before(small, small_ids, "small"))
    large_before_count = count_queries(large.engine, lambda: before(large, large_ids, "large"))
    assert small_before_count == large_before_count

    small_result = small.services.facts.ingest(holder["small_batch"])  # type: ignore[arg-type]
    large_result = large.services.facts.ingest(holder["large_batch"])  # type: ignore[arg-type]
    assert len(small_result.ack.applied) == 4
    assert len(large_result.ack.applied) == 12

    small_publish_count = count_queries(
        small.engine,
        lambda: holder["small_broadcast"].publish(small_result),  # type: ignore[attr-defined]
    )
    large_publish_count = count_queries(
        large.engine,
        lambda: holder["large_broadcast"].publish(large_result),  # type: ignore[attr-defined]
    )
    assert small_publish_count == large_publish_count


# --- frame equivalence on the ingest arm --------------------------------------------

#: Named "default-delivery" — the packaged default graph's own name (``bzh:`` test
#: convention across this suite) — so a plain ``POST /api/chunks`` minted after this graph
#: binds to it, with no ``graph_name`` field on the chunk-create route to select it by.
#:
#: Two nodes, not one: a migration records a ``chunk_migrations`` fact, never a transition
#: (``bzh:migration-not-transition``), so migrating straight off the entry node would leave
#: ``newest_transition()`` empty and no ``from_graph`` case to exercise. The leading
#: ``assess -> handoff`` transition is what ``prev_node`` resolves against, cross-graph,
#: once ``handoff -> graph:migrate-target`` re-pins the chunk.
_MIGRATE_SRC_YAML = """
name: default-delivery
entry: assess
nodes:
  assess:
    executor: runner
    prompt: Assess.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Hand off.
          to: handoff
  handoff:
    executor: runner
    prompt: Hand off.
    judgement:
      prompt: Assess.
      choices:
        migrate:
          description: Migrate.
          to: graph:migrate-target
"""

_MIGRATE_TARGET_YAML = """
name: migrate-target
entry: handle
nodes:
  handle:
    executor: runner
    prompt: Handle.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Done.
          to: done
"""


def _chunk_changed_frames(hub, *, since: int = 0) -> list[dict]:  # type: ignore[no-untyped-def]
    return [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == CHUNK_CHANGED]


def _all_frames(hub, *, since: int = 0) -> list[dict]:  # type: ignore[no-untyped-def]
    return [{"event": e["event"], **json.loads(e["data"])} for e in emitted_events(hub, since=since)]


def _status_of(hub, chunk_id: str) -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return str(resp.json()["status"])


def test_ingest_batch_frame_sequence_matches_per_fact_publish(tmp_path: Path) -> None:
    """An interleaved batch across three chunks — a lease re-mint, an escalation, a
    question asked-then-answered, and an escalation on a chunk with a cross-graph newest
    transition — proves the loaded-once-per-batch state reproduces exactly what
    independent per-fact reloads produced before this change (``bzh:bulk-reconstitution``):
    one frame per applied fact in batch order, each with its own cause and key, a
    question-asked frame just before its chunk-changed, the ``route_created:<id>`` key on
    the lease re-mint, ``prev_status`` fixed to the pre-batch snapshot, and — since publish
    only ever runs once ingest has landed every fact — every frame for the same chunk
    carrying identical post-batch ``status``/``node``/``prev_node``/``runner_id``/``graph_id``."""
    hub = build_hub(tmp_path)
    # Both custom graphs mint before any chunk does (``bzh:``-adjacent test convention):
    # the packaged default graph is itself named "default-delivery" and shares this
    # suite's ``FixedClock`` instant with any re-mint of that name, so "newest of that
    # name" only resolves deterministically to the fresh mint when no chunk (whose own id
    # allocation interleaves with the graphs') exists yet to perturb the tie-break.
    assert hub.client.post("/api/graphs", json={"definition_yaml": _MIGRATE_TARGET_YAML}).status_code == 201
    assert hub.client.post("/api/graphs", json={"definition_yaml": _MIGRATE_SRC_YAML}).status_code == 201

    chunk_l = _claim(hub, "L", runner_id="rL")
    chunk_q = _claim(hub, "Q", runner_id="rQ")

    # A migrated chunk: its newest transition landed in the source graph, but it is now
    # pinned to the target — the cross-graph ``from_graph`` case.
    chunk_m = ingest(hub, [{"source": "default", "ref": "M"}])
    claim_m = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_m, "runner_id": "rM", "workspace_id": "w-rM", "environment_ids": ["e"]},
    )
    assert claim_m.status_code == 201, claim_m.text
    assess_node_id = claim_m.json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_m, epoch=1, seq=1, runner_id="rM")
    handoff = hub.client.post(
        f"/api/fleet/chunks/{chunk_m}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "rM", "from_node_id": assess_node_id, "artifacts": []},
    )
    assert handoff.status_code == 200, handoff.text
    handoff_node_id = handoff.json()["next_envelope"]["node"]["node_id"]
    migrated = hub.client.post(
        f"/api/fleet/chunks/{chunk_m}/completions",
        json={"choice": "migrate", "epoch": 1, "runner_id": "rM", "from_node_id": handoff_node_id, "artifacts": []},
    )
    assert migrated.status_code == 200, migrated.text
    assert migrated.json()["outcome"] == "migrated"

    l_status_before = _status_of(hub, chunk_l)
    q_status_before = _status_of(hub, chunk_q)
    m_status_before = _status_of(hub, chunk_m)

    since = hub.events.latest_id()
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "fleet-batch",
            "facts": [
                {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_l, "epoch": 2}},
                {
                    "seq": 2,
                    "kind": "escalation.recorded",
                    "payload": {"chunk_id": chunk_q, "epoch": 1, "takeover_command": "cd wd && q"},
                },
                {
                    "seq": 3,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "qn_1",
                        "chunk_id": chunk_q,
                        "node_id": "nd_build",
                        "session_id": "sess-1",
                        "runner_id": "rQ",
                        "epoch": 1,
                        "question": "Which API?",
                        "options": ["rest", "graphql"],
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                },
                {"seq": 4, "kind": "answer.delivered", "payload": {"question_id": "qn_1", "chunk_id": chunk_q}},
                {
                    "seq": 5,
                    "kind": "escalation.recorded",
                    "payload": {"chunk_id": chunk_m, "epoch": 1, "takeover_command": "cd wd && m"},
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [1, 2, 3, 4, 5]

    frames = _all_frames(hub, since=since)
    kinds = [f["event"] for f in frames]
    assert kinds == [CHUNK_CHANGED, CHUNK_CHANGED, QUESTION_ASKED_EVENT, CHUNK_CHANGED, CHUNK_CHANGED, CHUNK_CHANGED]

    lease_frame, escalate_q_frame, question_asked_frame, question_frame, answer_frame, escalate_m_frame = frames

    assert lease_frame["chunk_id"] == chunk_l
    assert lease_frame["cause"] == "claimed"
    assert lease_frame["key"].startswith("route_created:")
    assert lease_frame["prev_status"] == l_status_before

    assert escalate_q_frame["chunk_id"] == chunk_q
    assert escalate_q_frame["cause"] == "escalated"
    assert escalate_q_frame["key"].startswith("escalations:")
    assert escalate_q_frame["prev_status"] == q_status_before

    assert question_asked_frame["chunk_id"] == chunk_q
    assert question_asked_frame["question_id"] == "qn_1"

    assert question_frame["chunk_id"] == chunk_q
    assert question_frame["cause"] == "question-asked"
    assert question_frame["key"] == "questions:qn_1"
    assert question_frame["prev_status"] == q_status_before

    assert answer_frame["chunk_id"] == chunk_q
    assert answer_frame["cause"] == "question-answered"
    assert answer_frame["key"] == "question_answers:qn_1"
    assert answer_frame["prev_status"] == q_status_before

    # publish() only runs after every fact in the batch has already landed, so Q's three
    # frames all reload the same final post-batch state — differing only in cause/key.
    for field in ("status", "node", "prev_node", "runner_id", "graph_id"):
        assert escalate_q_frame.get(field) == question_frame.get(field) == answer_frame.get(field)

    assert escalate_m_frame["chunk_id"] == chunk_m
    assert escalate_m_frame["cause"] == "escalated"
    assert escalate_m_frame["key"].startswith("escalations:")
    assert escalate_m_frame["prev_status"] == m_status_before
    # The cross-graph resolution: `prev_node` reads from the source graph (`assess`),
    # `node` from the chunk's post-migration pin (`handle`).
    assert escalate_m_frame["prev_node"] == "assess"
    assert escalate_m_frame["node"] == "handle"

    # A replay of the whole batch alongside one fresh fact: only the fresh one publishes.
    since_replay = hub.events.latest_id()
    replay = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "fleet-batch",
            "facts": [
                {"seq": 1, "kind": "lease.minted", "payload": {"chunk_id": chunk_l, "epoch": 2}},
                {"seq": 5, "kind": "escalation.recorded", "payload": {"chunk_id": chunk_m, "epoch": 1}},
                {"seq": 6, "kind": "lease.minted", "payload": {"chunk_id": chunk_l, "epoch": 3}},
            ],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["applied"] == [6]
    assert replay.json()["already_applied"] == [1, 5]
    replay_frames = _chunk_changed_frames(hub, since=since_replay)
    assert len(replay_frames) == 1
    assert replay_frames[0]["chunk_id"] == chunk_l


# --- singular sites keep their own statement counts ---------------------------------


def test_escalation_route_query_count_is_unaffected(tmp_path: Path) -> None:
    """The single-chunk escalation route (``ChunkChanged.before``/``publish``, the
    singular pair, still going through ``ChunkFrameState.load`` for its one chunk) issues
    the same statement count this refactor leaves untouched — a pinned baseline, not a
    batch-size comparison, since this route takes no batch."""
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub, "esc", runner_id="r1")

    def call() -> None:
        resp = hub.client.post(
            f"/api/fleet/chunks/{chunk_id}/escalations",
            json={"runner_id": "r1", "epoch": 1, "takeover_command": "cd wd && claude --resume"},
        )
        assert resp.status_code == 202, resp.text

    assert count_queries(hub.engine, call) == 72


def test_delete_routes_degrade_branch_query_count_is_unaffected(tmp_path: Path) -> None:
    """``DELETE /api/chunks/{id}``'s degrade branch (the gone-chunk read ``ChunkFrameState``
    skips ``from_graph``/route reads for, same as ``ChunkChanged.publish`` did) issues the
    same pinned statement count as before this refactor."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "del"}], promote=False)

    def call() -> None:
        resp = hub.client.request("DELETE", f"/api/chunks/{chunk_id}", json={})
        assert resp.status_code == 202, resp.text

    assert count_queries(hub.engine, call) == 67
