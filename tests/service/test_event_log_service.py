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
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import _forge, _free_port, _hub
from tests.service.support import mock_runner, service_gate, sse_tap
from tests.service.test_hub_service import _ingest, _stack

pytestmark = [pytest.mark.service, service_gate]


def _events(hub: httpx.Client, **params) -> list[dict]:
    resp = hub.get("/api/events", params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()["events"]


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
