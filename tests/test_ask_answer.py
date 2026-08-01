"""The ask/answer rendezvous at the hub (component tier) — MVP criterion 7.

Pins the hub half of the protocol against a fully-wired store:

* a forwarded ``question.asked`` (both the batched ``POST /events`` path the runner
  uses and the typed ``POST /questions`` route) lands a durable row, and the chunk
  derives **waiting_on_human** with the question surfaced on its detail;
* the answer is **first-write-wins CAS** — a racing second answer loses with 409 and
  is told who already answered — and the winning row flips the chunk back to running;
* the **return leg** (issue #165): the runner's ``answer.delivered`` fact derives
  ``delivered``/``delivered_at`` onto every question view, and the ``chunk-changed`` the
  same ingest already publishes is what refreshes the board's trail live;
* ``GET /questions`` lists only the open ones (the ``blizzard hub status`` surface).
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.events.broker import CHUNK_CHANGED
from tests.support import assert_all_timestamps_utc, build_hub, emitted_events, pointer_token

pytestmark = pytest.mark.component

_POINTER = {"source": "default", "ref": "7"}

_GRAPH_YAML = """
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
          to: deliver
    retries:
      max: 2
      exhausted: escalate
  deliver:
    executor: hub
    run:
      - command: "true"
    judgement:
      choices:
        success:
          description: Delivered.
          to: done
        failure:
          description: Failed to deliver.
          to: build
"""


def _claim(hub) -> str:  # type: ignore[no-untyped-def]
    assert hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def _ask(hub, chunk_id: str, *, question_id: str = "qn_1", question: str = "Which API?") -> None:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": question_id,
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": question,
            "options": ["rest", "graphql"],
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 201, resp.text


def test_forwarded_question_parks_chunk_and_surfaces(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"

    _ask(hub, chunk_id)

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "waiting_on_human"
    assert [q["question_id"] for q in detail["questions"]] == ["qn_1"]
    assert detail["questions"][0]["options"] == ["rest", "graphql"]

    # GET /questions is the fleet open-question surface (hub status).
    open_resp = hub.client.get("/api/questions")
    open_qs = open_resp.json()
    assert [q["question_id"] for q in open_qs] == ["qn_1"]
    assert_all_timestamps_utc(open_resp.json())  # bzh:utc-instants — asked_at

    # GET /questions/{id} is the runner's answer poll — open until answered.
    poll = hub.client.get("/api/fleet/questions/qn_1").json()
    assert poll["answered"] is False


def test_ask_and_answer_carry_distinct_causes(tmp_path: Path) -> None:
    """The two routes share one call site (``questions.py``'s ``_publish``, issue #212) —
    asserted separately so a hardcoded or defaulted cause on either route shows up here."""
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)

    before_ask = int(emitted_events(hub)[-1]["id"])
    _ask(hub, chunk_id)
    ask_frames = [json.loads(e["data"]) for e in emitted_events(hub, since=before_ask) if e["event"] == CHUNK_CHANGED]
    assert ask_frames[-1]["cause"] == "question-asked"

    before_answer = int(emitted_events(hub)[-1]["id"])
    answer = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})
    assert answer.status_code == 201, answer.text
    answer_frames = [
        json.loads(e["data"]) for e in emitted_events(hub, since=before_answer) if e["event"] == CHUNK_CHANGED
    ]
    assert answer_frames[-1]["cause"] == "question-answered"


def test_ask_question_normalizes_a_naive_asked_at(tmp_path: Path) -> None:
    """Insurance on the typed route too (issue #28, ``bzh:utc-instants``): ``_parse``
    coerces a naive ``asked_at`` to UTC rather than storing it (and later re-emitting
    it) naive."""
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id, question_id="qn_naive")
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_also_naive",
            "chunk_id": chunk_id,
            "node_id": "nd_build",
            "session_id": "sess-1",
            "runner_id": "r1",
            "epoch": 1,
            "question": "Which API?",
            "options": [],
            "asked_at": "2026-07-13T00:00:00",  # naive — no offset
        },
    )
    assert resp.status_code == 201, resp.text
    poll = hub.client.get("/api/fleet/questions/qn_also_naive").json()
    assert poll["asked_at"] == "2026-07-13T00:00:00+00:00"


def test_question_asked_via_events_batch_lands(tmp_path: Path) -> None:
    # The store-and-forward path the reconciliation loop actually uses.
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 5,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "qn_batch",
                        "chunk_id": chunk_id,
                        "node_id": "nd_build",
                        "session_id": "sess-1",
                        "epoch": 1,
                        "question": "batch?",
                        "options": [],
                        "asked_at": "2026-07-13T00:00:00+00:00",
                    },
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [5]
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "waiting_on_human"


def test_question_asked_via_events_batch_normalizes_a_naive_asked_at(tmp_path: Path) -> None:
    """Legacy-buffered-payload insurance (issue #28, ``bzh:utc-instants``).

    A runner's outbound buffer can still hold — and later deliver — a naive
    ``asked_at`` string minted before the runner's own upgrade; ``_parse_at`` coerces it
    to UTC rather than storing (and later re-emitting) a naive instant.
    """
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 5,
                    "kind": "question.asked",
                    "payload": {
                        "question_id": "qn_legacy",
                        "chunk_id": chunk_id,
                        "node_id": "nd_build",
                        "session_id": "sess-1",
                        "epoch": 1,
                        "question": "batch?",
                        "options": [],
                        "asked_at": "2026-07-13T00:00:00",  # naive — no offset
                    },
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [5]

    poll = hub.client.get("/api/fleet/questions/qn_legacy").json()
    assert poll["asked_at"] == "2026-07-13T00:00:00+00:00"


def test_answer_first_write_wins_second_gets_409_with_winner(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)

    # `answered_by` in the body is a spoof attempt — issue #91 overwrites it with the
    # resolved session identity, `"operator"` under the default `auth.mode = "none"`.
    first = hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest", "answered_by": "alice"})
    assert first.status_code == 201, first.text
    assert first.json() == {
        "won": True,
        "question_id": "qn_1",
        "answer": "rest",
        "answered_by": "operator",
        "answered_at": first.json()["answered_at"],
    }
    assert_all_timestamps_utc(first.json())  # bzh:utc-instants — answered_at

    # A racing second answer loses the CAS and is told who already answered.
    second = hub.client.post("/api/questions/qn_1/answers", json={"answer": "graphql", "answered_by": "bob"})
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["won"] is False
    assert body["answered_by"] == "operator"
    assert body["answer"] == "rest"

    # The winning answer flips the chunk back out of waiting_on_human. The question row
    # itself *stays* on the detail carrying its trail (issue #165) — dropping it was what
    # left an answerer with no evidence their answer went anywhere — but it is answered,
    # and not yet delivered: no runner has reported the resume.
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert detail["status"] == "running"
    assert [(q["question_id"], q["answered"], q["answer"], q["delivered"]) for q in detail["questions"]] == [
        ("qn_1", True, "rest", False)
    ]
    assert detail["questions"][0]["delivered_at"] is None
    assert hub.client.get("/api/questions").json() == []
    poll = hub.client.get("/api/fleet/questions/qn_1").json()
    assert poll["answered"] is True
    assert poll["answer"] == "rest"


def _deliver(hub, chunk_id: str, *, question_id: str = "qn_1", seq: int = 9):  # type: ignore[no-untyped-def]
    """Push the runner's ``answer.delivered`` fact — the resume-with-answer ran."""
    return hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {"seq": seq, "kind": "answer.delivered", "payload": {"chunk_id": chunk_id, "question_id": question_id}}
            ],
        },
    )


def test_answer_delivered_fact_is_accepted(tmp_path: Path) -> None:
    # The runner reports answer.delivered up after resuming; the hub records it (board
    # detail) rather than rejecting an unknown kind.
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})
    resp = _deliver(hub, chunk_id)
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [9]
    assert resp.json()["rejected"] == []


def test_answer_delivered_surfaces_the_return_trip_on_the_question_view(tmp_path: Path) -> None:
    """The delivered fact is *readable*, not merely stored (issue #165).

    Before this, ``answer.delivered`` landed in ``answer_deliveries`` and went no further
    — nothing derived it onto the wire, so no client could tell a delivered answer from
    one still sitting at the hub. Now every question surface carries the pair.
    """
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})
    assert _deliver(hub, chunk_id).status_code == 200

    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    question = detail["questions"][0]
    assert question["delivered"] is True
    assert question["delivered_at"] is not None
    assert_all_timestamps_utc(detail["questions"])  # bzh:utc-instants — delivered_at

    # The runner's own answer poll renders through the same view, so it carries it too.
    assert hub.client.get("/api/fleet/questions/qn_1").json()["delivered"] is True


def test_a_replayed_delivery_keeps_the_first_delivered_at(tmp_path: Path) -> None:
    """``answer_deliveries`` is append-only with no per-question uniqueness, so the view
    reads the **earliest** row: a second delivery is a re-delivery, not a correction of
    when the agent actually woke."""
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})
    assert _deliver(hub, chunk_id, seq=9).status_code == 200
    first = hub.client.get(f"/api/chunks/{chunk_id}").json()["questions"][0]["delivered_at"]

    hub.clock.advance(timedelta(minutes=1))
    # A fresh seq, so the high-water mark does not dedupe it — a genuine second row.
    assert _deliver(hub, chunk_id, seq=10).status_code == 200

    questions = hub.client.get(f"/api/chunks/{chunk_id}").json()["questions"]
    # Exactly one row back, not one per delivery: the read pre-aggregates deliveries to
    # the earliest instant per question, so a second row cannot multiply the question.
    assert [q["question_id"] for q in questions] == ["qn_1"]
    assert questions[0]["delivered_at"] == first


def test_landing_a_delivered_fact_publishes_chunk_changed_for_the_trail(tmp_path: Path) -> None:
    """AC 2 — the delivered trail refreshes off the **existing** ``chunk-changed`` frame,
    with no event type of its own.

    A delivery moves no derived status, so the frame repeats the status the board already
    shows. That is not a reason to mint a second event type: the ingest publishes
    ``chunk-changed`` on the *fact*, not on a status change, and the board's live-update
    spine keys off a frame arriving rather than off the status differing — so the chunk
    read is staled and the dock re-reads the now-delivered question. This test pins the
    hub half of that; ``fleet-live.spec.ts`` pins the client half against a repeated
    status. Together they are why the dedicated frame the issue deferred is unnecessary.
    """
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    hub.client.post("/api/questions/qn_1/answers", json={"answer": "rest"})
    before = int(emitted_events(hub)[-1]["id"])  # the last id published before the delivery
    status_before = hub.client.get(f"/api/chunks/{chunk_id}").json()["status"]

    assert _deliver(hub, chunk_id).status_code == 200

    published = emitted_events(hub, since=before)
    frames = [(e["event"], json.loads(e["data"])) for e in published]
    # The frame naming this chunk is what the board re-reads on — and it is the *only*
    # frame the delivery emits, so a regression that adds a second one shows up here.
    assert [event for event, _ in frames] == [CHUNK_CHANGED]
    data = frames[0][1]
    assert data["chunk_id"] == chunk_id
    # It genuinely carries no news by itself: the status is unchanged across the delivery
    # — cause names the fact that drove the frame regardless (issue #212).
    assert data["status"] == status_before
    assert data["prev_status"] == status_before
    assert data["cause"] == "question-answered"
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == status_before


def test_each_question_carries_its_own_delivery_instant(tmp_path: Path) -> None:
    """Two questions on one chunk, delivered at different times, keep their own instants —
    and an undelivered third stays undelivered.

    The read pre-aggregates ``answer_deliveries`` per question. Aggregating without that
    per-question grouping still yields one row and still passes a single-question test,
    while silently smearing one arbitrary instant across every question on the chunk.
    """
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    for qid in ("qn_1", "qn_2", "qn_3"):
        _ask(hub, chunk_id, question_id=qid)
        hub.client.post(f"/api/questions/{qid}/answers", json={"answer": qid})

    assert _deliver(hub, chunk_id, question_id="qn_1", seq=20).status_code == 200
    hub.clock.advance(timedelta(minutes=5))
    assert _deliver(hub, chunk_id, question_id="qn_2", seq=21).status_code == 200
    # qn_3 is answered but never delivered.

    by_id = {q["question_id"]: q for q in hub.client.get(f"/api/chunks/{chunk_id}").json()["questions"]}
    assert [by_id[q]["delivered"] for q in ("qn_1", "qn_2", "qn_3")] == [True, True, False]
    assert by_id["qn_3"]["delivered_at"] is None
    # The two delivered instants are each their own, five minutes apart — not one shared.
    assert by_id["qn_1"]["delivered_at"] != by_id["qn_2"]["delivered_at"]
    assert by_id["qn_1"]["delivered_at"] < by_id["qn_2"]["delivered_at"]


def test_every_question_read_derives_delivery_the_same_way(tmp_path: Path) -> None:
    """A delivery is derived, never assumed away, on all three question reads.

    ``record_answer_delivered`` has no answer-row precondition and the FK is on
    ``question_id`` alone, so a malformed or replayed runner batch *can* land a delivery
    for a question with no answer. Nothing about that is normal — but the open-question
    list must not answer differently from the chunk detail about the same row, which is
    what hardcoding "open, therefore never delivered" would do.
    """
    hub = build_hub(tmp_path)
    chunk_id = _claim(hub)
    _ask(hub, chunk_id)
    # Deliberately no answer: the delivery lands against a still-open question.
    assert _deliver(hub, chunk_id).status_code == 200

    still_open = hub.client.get("/api/questions").json()
    assert [q["question_id"] for q in still_open] == ["qn_1"], "no answer row, so it is still open"
    on_detail = hub.client.get(f"/api/chunks/{chunk_id}").json()["questions"][0]
    poll = hub.client.get("/api/fleet/questions/qn_1").json()

    # All three agree — open, and delivered — rather than the list alone claiming otherwise.
    assert [still_open[0]["answered"], on_detail["answered"], poll["answered"]] == [False, False, False]
    assert [still_open[0]["delivered"], on_detail["delivered"], poll["delivered"]] == [True, True, True]


def test_answer_unknown_question_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/questions/qn_missing/answers", json={"answer": "x"})
    assert resp.status_code == 404


def test_question_on_unknown_chunk_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post(
        "/api/questions",
        json={
            "question_id": "qn_x",
            "chunk_id": "ch_missing",
            "runner_id": "r1",
            "epoch": 1,
            "question": "?",
            "asked_at": "2026-07-13T00:00:00+00:00",
        },
    )
    assert resp.status_code == 404
