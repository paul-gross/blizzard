"""The operational event feed over the wire — SSE fan-out + fold read-back (issue #125,
Phase 2, service tier).

The wire leg the lower tiers cannot prove: a real running hub, driven by the
``blizzard-mock`` mock runner's ``/_drive/report-event`` verb (its ``event.recorded``
counterpart), folds the fact into the ``event_log`` and re-broadcasts it on the live SSE
spine. A subscriber connected *before* the act receives ``event-logged`` **exactly once**
(0 fails a dropped publish, 2 a broken idempotency guard) and the event reads back off the
live ``GET /api/events`` — the mock-runner→live-hub direction, modeled on
``test_usage_service.py``. Idempotency on the per-runner seq is pinned by a direct
fixed-seq replay.

The real runner's own emission (and its store-and-forward buffering through a hub outage)
lands in Phase 3, where the real runner emits these facts; here the mock runner stands in
so the hub fold + SSE fan-out are provable independently of that work.

Also carries the ``GET /api/activity`` service-tier proof (issue #213, Phase 5). This
is the tier that reads
:class:`~blizzard.wire.activity.ActivityView`'s field names off a live JSON response
rather than an in-process Python object, over several distinct
:data:`~blizzard.hub.events.broker.ChunkChangeCause` families driven through the mock
runner and a chunk-level pause.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import _forge, _free_port, _hub
from tests.service.support import mock_runner, service_gate, sse_tap
from tests.service.test_hub_service import _graph_yaml, _ingest, _stack

pytestmark = [pytest.mark.service, service_gate]


def _events(hub: httpx.Client, **params) -> list[dict]:
    resp = hub.get("/api/events", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


def _activity(hub: httpx.Client, **params) -> list[dict]:
    resp = hub.get("/api/activity", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["activity"]


def test_a_driven_event_folds_into_the_log_and_fans_out_over_sse_exactly_once(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        chunk_id = _ingest(forge, hub, "surface a worker-lost event")

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")

            # Subscribe before the act, so what arrives after is fan-out and not replay.
            with sse_tap(hub_port) as tap:
                drove = runner.post(
                    "/_drive/report-event",
                    json={
                        "severity": "critical",
                        "kind": "worker-lost",
                        "message": "worker exited without a session-end",
                        "chunk_id": chunk_id,
                        "node_name": "build",
                        "detail": {"via": "advance", "reason": "failed"},
                    },
                ).json()
                assert drove["drove"] is True, drove
                assert drove["status"] == 200, drove
                live = tap.collect(window=6.0)
            assert live.count("event-logged") == 1, live

        # ...and the event reads back off the live hub, folded into the feed.
        feed = _events(hub)
        lost = [e for e in feed if e["kind"] == "worker-lost"]
        assert len(lost) == 1, feed
        assert lost[0]["severity"] == "critical"
        assert lost[0]["chunk_id"] == chunk_id
        assert lost[0]["node_name"] == "build"
        assert lost[0]["detail"] == {"via": "advance", "reason": "failed"}


def test_a_replayed_event_seq_folds_exactly_once(tmp_path: Path) -> None:
    """The fold is idempotent on the per-runner outbound seq: a re-pushed seq lands
    nothing twice (the same guard the usage store-and-forward test proves for usage)."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port), _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        # A runner-scoped event (no chunk_id — no FK dependency) pushed directly with a
        # fixed seq, twice. The second push is at/below the high-water mark, so it is
        # re-acked and not re-applied.
        batch = {
            "runner_id": "event-pusher",
            "facts": [
                {
                    "seq": 1,
                    "kind": "event.recorded",
                    "payload": {"severity": "warning", "kind": "command-failed", "message": "git push failed"},
                }
            ],
        }
        assert hub.post("/api/fleet/events", json=batch).status_code == 200
        assert hub.post("/api/fleet/events", json=batch).status_code == 200  # replay

        feed = _events(hub)
        failed = [e for e in feed if e["kind"] == "command-failed"]
        assert len(failed) == 1, feed
        assert failed[0]["severity"] == "warning"
        assert failed[0]["chunk_id"] is None


def test_activity_backfill_merges_several_cause_families_bounded_and_newest_first(tmp_path: Path) -> None:
    """``GET /api/activity`` against a real running hub (issue #213, Phase 5) — the wire
    leg the component tier's in-process ``ActivityRow``/``ActivityView`` objects cannot
    prove: that the route's JSON actually serializes the field names
    :class:`~blizzard.wire.activity.ActivityView` declares (``type``, ``key``, ``at``,
    ``cause``, …), off several distinct :data:`~blizzard.hub.events.broker.ChunkChangeCause`
    families driven through the mock runner + a chunk-level pause, merged with an
    ``event-logged`` row and a ``runner-changed`` row, newest-first, bounded by ``limit``,
    and windowed by ``since``.

    Drives (all real wire calls against the running hub, mock-runner/mock-forge
    counterpart): ingest+promote (``minted``/``promoted``), claim+complete
    (``claimed``/``node-completed``), escalate (``escalated``), a chunk-level pause+resume
    (``paused``/``resumed``), an operational event report (``event-logged``), and the
    runner's own local pause (``runner-changed``/``locally-paused``).
    """
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "several cause families")  # -> "minted" + "promoted"

        # A real wall-clock watermark between the mint/promote pair above and everything
        # below — the service tier runs the hub against the real system clock (no
        # injected fake clock, unlike the component tier), so this is a genuine `since`
        # boundary a narrowed read can be checked against.
        time.sleep(0.05)
        since_marker = datetime.now(UTC)
        time.sleep(0.05)

        with mock_runner(bin_dir, _free_port(), hub_port) as runner:
            runner.post("/_drive/register")
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True  # -> "claimed" (route_created)

            complete = runner.post("/_drive/complete", json={"chunk_id": chunk_id, "choice": "pass"}).json()
            assert complete["response"]["outcome"] == "next", complete  # -> "node-completed" (transitions)

            escalate = runner.post("/_drive/escalate", json={"chunk_id": chunk_id}).json()
            assert escalate["drove"] is True, escalate  # -> "escalated" (escalations)

            drove_event = runner.post(
                "/_drive/report-event",
                json={
                    "severity": "warning",
                    "kind": "activity-feed-probe",
                    "message": "a probed operational event",
                    "chunk_id": chunk_id,
                },
            ).json()
            assert drove_event["status"] == 200, drove_event  # -> "event-logged"

            local_pause = runner.post("/_drive/pause", json={"by": "operator"}).json()
            assert local_pause["status"] == 200, local_pause  # -> "runner-changed" / "locally-paused"

        assert hub.post(f"/api/chunks/{chunk_id}/pause", json={"by": "operator"}).status_code == 202
        assert hub.post(f"/api/chunks/{chunk_id}/resume", json={"by": "operator"}).status_code == 202
        # -> "paused" then "resumed" (chunk_pause_facts)

        feed = _activity(hub)
        # Every row carries the wire field names ActivityView declares, off a live
        # response — the schema a component test's Python objects never round-trip
        # through JSON to prove.
        for row in feed:
            assert set(row) <= {
                "type",
                "key",
                "at",
                "chunk_id",
                "status",
                "prev_status",
                "node",
                "prev_node",
                "runner_id",
                "cause",
                "graph_id",
                "severity",
                "kind",
                "by",
                "reason",
            }, row
            assert isinstance(row["type"], str) and row["type"]
            assert isinstance(row["key"], str) and row["key"]
            assert isinstance(row["at"], str) and row["at"]

        chunk_rows = [r for r in feed if r["chunk_id"] == chunk_id]
        causes = {r["cause"] for r in chunk_rows if r["type"] == "chunk-changed"}
        assert {"minted", "promoted", "claimed", "node-completed", "escalated", "paused", "resumed"} <= causes, feed

        event_rows = [r for r in feed if r["type"] == "event-logged" and r["kind"] == "activity-feed-probe"]
        assert len(event_rows) == 1, feed
        assert event_rows[0]["severity"] == "warning"
        assert event_rows[0]["chunk_id"] == chunk_id

        runner_rows = [r for r in feed if r["type"] == "runner-changed" and r["kind"] == "locally-paused"]
        assert len(runner_rows) == 1, feed
        assert runner_rows[0]["by"] == "operator"

        # Newest-first, and bounded — never more than `limit` rows even when a lot less
        # than `limit` were actually driven.
        ats = [row["at"] for row in feed]
        assert ats == sorted(ats, reverse=True), feed
        assert len(feed) <= 200

        limited = _activity(hub, limit=1)
        assert len(limited) == 1
        assert limited[0]["at"] == ats[0]

        # The 24h `since` window, exercised directly: a narrow `since` set strictly
        # between "minted"/"promoted" and everything driven afterwards excludes the
        # former and keeps the latter.
        narrowed = _activity(hub, since=since_marker.isoformat())
        narrowed_chunk_causes = {
            r["cause"] for r in narrowed if r["chunk_id"] == chunk_id and r["type"] == "chunk-changed"
        }
        assert "minted" not in narrowed_chunk_causes, narrowed
        assert "promoted" not in narrowed_chunk_causes, narrowed
        assert {"claimed", "node-completed", "escalated", "paused", "resumed"} <= narrowed_chunk_causes, narrowed
        assert any(r["type"] == "event-logged" and r["kind"] == "activity-feed-probe" for r in narrowed), narrowed
        assert any(r["type"] == "runner-changed" and r["kind"] == "locally-paused" for r in narrowed), narrowed
