"""The operational event feed over the wire — SSE fan-out + fold read-back (#125 P2).

A real hub, driven by the mock runner's ``/_drive/report-event`` verb, folds the fact
into ``event_log`` and fans it out over SSE exactly once; also carries the
``GET /api/activity`` service-tier proof (#213 P5).
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
        # A runner-scoped event pushed directly with a fixed seq, twice — the second is
        # at/below the high-water mark, so it is re-acked and not re-applied.
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
    """``GET /api/activity`` (issue #213) serializes ActivityView's real field names over
    the wire, merges chunk/event/runner cause families newest-first, bounded by ``limit``
    and windowed by ``since``."""
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "several cause families")  # -> "minted" + "promoted"

        # A real wall-clock watermark between the mint/promote pair above and everything
        # below — a genuine `since` boundary a narrowed read can be checked against.
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
        # response — never round-tripped through JSON at the component tier.
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

        # The 24h `since` window: a narrow `since` set strictly between "minted"/"promoted"
        # and everything after excludes the former, keeps the latter.
        narrowed = _activity(hub, since=since_marker.isoformat())
        narrowed_chunk_causes = {
            r["cause"] for r in narrowed if r["chunk_id"] == chunk_id and r["type"] == "chunk-changed"
        }
        assert "minted" not in narrowed_chunk_causes, narrowed
        assert "promoted" not in narrowed_chunk_causes, narrowed
        assert {"claimed", "node-completed", "escalated", "paused", "resumed"} <= narrowed_chunk_causes, narrowed
        assert any(r["type"] == "event-logged" and r["kind"] == "activity-feed-probe" for r in narrowed), narrowed
        assert any(r["type"] == "runner-changed" and r["kind"] == "locally-paused" for r in narrowed), narrowed
