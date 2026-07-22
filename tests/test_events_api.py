"""``GET /api/events`` — the operational event feed read (issue #125, Phase 1, component
tier).

Off a real, migrated hub app this proves the acceptance criteria AC#2 and AC#5: the read
returns the ``event_log`` unified with every currently-open escalation (a superseded one
excluded), newest-and-most-severe first, honouring the ``severity`` / ``runner_id`` /
``chunk_id`` / ``since`` filters and the bounded default page — and a malformed ``since``
422s. Events and escalations are seeded through the domain store, exactly the path the
runner-fed fold (Phase 2) will write.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.store.internal.chunk_store import ChunkStore
from tests.support import build_hub, seed_chunk, seed_graph

pytestmark = pytest.mark.component


def _events(hub, **params) -> list[dict]:  # type: ignore[no-untyped-def]
    resp = hub.client.get("/api/events", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


def test_events_feed_unifies_open_escalations_filtered_and_ordered(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    store = ChunkStore(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        for cid in ("ch_a", "ch_b", "ch_c"):
            seed_chunk(conn, cid, graph_id="gr_1", at=t0)

    def at(sec: int):  # type: ignore[no-untyped-def]
        return t0 + timedelta(seconds=sec)

    # event_log rows across two runners and two chunks.
    store.record_event(
        severity="info",
        kind="attempt-abandoned",
        runner_id="r1",
        chunk_id="ch_a",
        lease_id=None,
        node_name="build",
        message="abandoned",
        detail=None,
        at=at(1),
    )
    store.record_event(
        severity="warning",
        kind="attempt-failed",
        runner_id="r1",
        chunk_id="ch_a",
        lease_id="l1",
        node_name="build",
        message="retried",
        detail={"via": "advance"},
        at=at(2),
    )
    store.record_event(
        severity="critical",
        kind="worker-lost",
        runner_id="r2",
        chunk_id="ch_b",
        lease_id="l2",
        node_name="review",
        message="lost",
        detail=None,
        at=at(3),
    )

    # ch_c: an OPEN escalation (projects into the feed as needs-human/critical).
    store.record_escalation("ch_c", epoch=1, takeover_command="cd c && resume", at=at(4))
    # ch_a: an escalation SUPERSEDED by a later lease mint -> excluded from the feed.
    store.record_escalation("ch_a", epoch=1, takeover_command="cd a && resume", at=at(1))
    store.record_lease("ch_a", epoch=2, runner_id="r1", at=at(5))

    feed = _events(hub)
    # Severity-then-recency: critical band first (worker-lost t3 vs needs-human t4 -> needs-human
    # newer), then the warning, then the info. The superseded ch_a escalation is absent.
    assert [(e["severity"], e["kind"]) for e in feed] == [
        ("critical", "needs-human"),
        ("critical", "worker-lost"),
        ("warning", "attempt-failed"),
        ("info", "attempt-abandoned"),
    ]
    # detail round-trips.
    assert next(e for e in feed if e["kind"] == "attempt-failed")["detail"] == {"via": "advance"}
    # The projected escalation names its chunk.
    assert next(e for e in feed if e["kind"] == "needs-human")["chunk_id"] == "ch_c"

    # Filters.
    assert [e["kind"] for e in _events(hub, severity="critical")] == ["needs-human", "worker-lost"]
    assert [e["kind"] for e in _events(hub, runner_id="r1")] == ["attempt-failed", "attempt-abandoned"]
    assert [e["kind"] for e in _events(hub, chunk_id="ch_a")] == ["attempt-failed", "attempt-abandoned"]
    assert [e["kind"] for e in _events(hub, chunk_id="ch_c")] == ["needs-human"]
    assert {e["kind"] for e in _events(hub, since=iso_utc(at(3)))} == {"needs-human", "worker-lost"}
    # Bounded.
    assert len(_events(hub, limit=1)) == 1


def test_malformed_since_422s(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get("/api/events", params={"since": "not-a-date"})
    assert resp.status_code == 422, resp.text


def test_naive_since_with_open_escalation_does_not_500(tmp_path: Path) -> None:
    # A well-formed but tz-NAIVE `since` (an offset-less ISO string — an ordinary client /
    # date-picker input) must not 500 when the feed projects an open escalation. The
    # escalation's `recorded_at` is tz-aware, so an un-coerced naive `since` raised
    # `TypeError: can't compare offset-naive and offset-aware datetimes` at the projection
    # filter; the store half was masked by `UtcDateTime`, so this only surfaced with an
    # escalation present. The controller now coerces `since` with `as_utc`.
    hub = build_hub(tmp_path)
    store = ChunkStore(hub.engine, hub.clock)
    t0 = hub.clock.now()
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=t0)
        seed_chunk(conn, "ch_c", graph_id="gr_1", at=t0)
    store.record_escalation("ch_c", epoch=1, takeover_command="cd c && resume", at=t0)
    # `2020-01-01T00:00:00` — valid ISO-8601, no timezone offset, well before the escalation.
    feed = _events(hub, since="2020-01-01T00:00:00")
    assert [e["kind"] for e in feed] == ["needs-human"]
