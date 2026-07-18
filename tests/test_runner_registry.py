"""The fleet registry — register, heartbeat, liveness, and the pause brake (component tier).

Drives the real hub over a tmp store: ``POST /runners`` registers,
``POST /runners/{id}/heartbeats`` refreshes liveness, ``GET /runners`` lists the fleet
with **derived** online/offline and paused, and ``POST /runners/{id}/pause`` / ``/resume``
set the operator's brake. Liveness and paused are never stored columns, so the
assertions drive the clock and read the derived surface.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.registry import STALE_AFTER
from tests.support import HubHarness, assert_all_timestamps_utc, build_hub, emitted_events

pytestmark = pytest.mark.component


def _register(hub: HubHarness, runner_id: str = "runner-a", workspace_id: str = "ws-a") -> dict:
    resp = hub.client.post("/api/runners", json={"runner_id": runner_id, "workspace_id": workspace_id})
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_register_is_idempotent_upsert(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert _register(hub)["first_registration"] is True
    again = hub.client.post("/api/runners", json={"runner_id": "runner-a", "workspace_id": "ws-b"})
    assert again.json()["first_registration"] is False
    # The re-register updated the workspace binding and refreshed last_seen.
    view = hub.client.get("/api/runners/runner-a").json()
    assert view["workspace_id"] == "ws-b"


def test_list_runners_derives_online_and_paused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    resp = hub.client.get("/api/runners")
    runners = resp.json()["runners"]
    assert len(runners) == 1
    assert runners[0]["runner_id"] == "runner-a"
    assert runners[0]["online"] is True  # just seen, at the fixed clock now
    # Two brakes, reported apart (issue #43): neither is on for a fresh runner.
    assert runners[0]["hub_paused"] is False
    assert runners[0]["locally_paused"] is False
    assert_all_timestamps_utc(resp.json())  # bzh:utc-instants — registered_at, last_seen_at


def test_liveness_goes_offline_when_stale_and_heartbeat_refreshes(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    hub.clock.advance(STALE_AFTER + timedelta(seconds=1))
    assert hub.client.get("/api/runners/runner-a").json()["online"] is False

    hb = hub.client.post("/api/runners/runner-a/heartbeats")
    assert hb.status_code == 204
    assert hub.client.get("/api/runners/runner-a").json()["online"] is True


def test_heartbeat_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/runners/ghost/heartbeats").status_code == 404


def test_get_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.get("/api/runners/ghost").status_code == 404


def test_pause_and_resume_flip_the_derived_brake(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    paused = hub.client.post("/api/runners/runner-a/pause", json={"by": "alice"})
    assert paused.status_code == 200
    assert paused.json()["hub_paused"] is True
    assert hub.client.get("/api/runners/runner-a").json()["hub_paused"] is True
    # The fleet's brake is not the runner's own: pausing here leaves that one alone.
    assert hub.client.get("/api/runners/runner-a").json()["locally_paused"] is False

    resumed = hub.client.post("/api/runners/runner-a/resume", json={"by": "alice"})
    assert resumed.status_code == 200
    assert resumed.json()["hub_paused"] is False
    assert hub.client.get("/api/runners/runner-a").json()["hub_paused"] is False


def test_pause_unknown_runner_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/runners/ghost/pause", json={"by": "op"}).status_code == 404


def test_registry_changes_emit_runner_changed_events(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    hub.client.post("/api/runners/runner-a/pause", json={"by": "op"})
    events = emitted_events(hub)
    assert [e["event"] for e in events] == ["runner-changed", "runner-changed"]
    assert all("runner-a" in e["data"] for e in events)


# --------------------------------------------------------------------------- #
# The runner's own brake, reported up (issue #43)
# --------------------------------------------------------------------------- #
#
# The hub never sets this one — it arrives as a fact through the runner's outbound buffer
#  and the hub only reads it. These assert it lands, that it is genuinely separate
# from the fleet's brake, and that the board is told (the hub can only render what it
# holds, and a fact that lands invisibly is a runner shown as claiming when it has stopped).


def _report_local_pause(
    hub: HubHarness,
    *,
    paused: bool,
    seq: int = 1,
    runner_id: str = "runner-a",
    by: str = "alice",
    reason: str | None = None,
) -> dict:
    kind = "runner.locally_paused" if paused else "runner.locally_resumed"
    payload: dict[str, object] = {"runner_id": runner_id, "by": by}
    if reason is not None:
        payload["reason"] = reason
    resp = hub.client.post(
        "/api/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": kind, "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_reported_local_pause_lands_and_is_separate_from_the_hubs_brake(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)

    assert _report_local_pause(hub, paused=True)["applied"] == [1]
    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused"] is True
    assert view["hub_paused"] is False  # the fleet never paused it — the runner did


def test_a_reported_local_resume_clears_only_the_local_brake(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    _register(hub)
    hub.client.post("/api/runners/runner-a/pause", json={"by": "alice"})  # the fleet's brake too
    _report_local_pause(hub, paused=True, seq=1)

    _report_local_pause(hub, paused=False, seq=2)
    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused"] is False
    assert view["hub_paused"] is True  # untouched — each brake is cleared where it was set


def test_a_ceiling_pause_reason_rides_the_report_and_lands_on_the_view(tmp_path: Path) -> None:
    """A spend-ceiling escalation's cause (issue #61) round-trips runner -> hub and is
    distinguishable from a manual pause: `by` names "runner-ceiling" and `reason` carries
    the composed ceiling+spend string, exactly the payload `check_spend_ceiling` composes."""
    hub = build_hub(tmp_path)
    _register(hub)

    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"
    _report_local_pause(hub, paused=True, by="runner-ceiling", reason=reason)

    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused"] is True
    assert view["locally_paused_by"] == "runner-ceiling"
    assert view["locally_paused_reason"] == reason


def test_a_manual_pause_carries_no_reason_and_renders_bare(tmp_path: Path) -> None:
    """A manual `blizzard runner pause` payload carries no `reason` key at all — the
    column reads back `None` rather than some stale or fabricated value."""
    hub = build_hub(tmp_path)
    _register(hub)

    _report_local_pause(hub, paused=True, by="operator")

    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused"] is True
    assert view["locally_paused_by"] == "operator"
    assert view["locally_paused_reason"] is None


def test_a_local_resume_clears_the_reason_alongside_the_brake(tmp_path: Path) -> None:
    """Once resumed, a stale ceiling reason must not keep rendering — the cause is nulled
    out together with `locally_paused`, not just left dangling on the old fact."""
    hub = build_hub(tmp_path)
    _register(hub)
    _report_local_pause(hub, paused=True, seq=1, by="runner-ceiling", reason="spend ceiling $5.00 reached")

    _report_local_pause(hub, paused=False, seq=2, by="operator")

    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused"] is False
    assert view["locally_paused_by"] is None
    assert view["locally_paused_reason"] is None


def test_a_replayed_ceiling_pause_records_the_reason_exactly_once(tmp_path: Path) -> None:
    """Idempotent ingest (the high-water mark) applies a re-delivered ceiling pause fact
    only once — a replay must not double-record or corrupt the landed reason."""
    hub = build_hub(tmp_path)
    _register(hub)
    reason = "spend ceiling $5.00 reached over the trailing 24h (spend $7.00)"

    first = _report_local_pause(hub, paused=True, seq=1, by="runner-ceiling", reason=reason)
    assert first["applied"] == [1]
    replay = _report_local_pause(hub, paused=True, seq=1, by="runner-ceiling", reason=reason)
    assert replay["applied"] == [] and replay["already_applied"] == [1]

    view = hub.client.get("/api/runners/runner-a").json()
    assert view["locally_paused_by"] == "runner-ceiling"
    assert view["locally_paused_reason"] == reason


def test_a_reported_local_pause_publishes_runner_changed(tmp_path: Path) -> None:
    """Runner-scoped facts carry no chunk_id, so the chunk-changed path would skip them."""
    hub = build_hub(tmp_path)
    _register(hub)
    before = len(emitted_events(hub))

    _report_local_pause(hub, paused=True)
    fresh = emitted_events(hub, since=before)
    assert [e["event"] for e in fresh] == ["runner-changed"]
    assert "runner-a" in fresh[0]["data"]


def test_a_replayed_local_pause_is_not_reapplied(tmp_path: Path) -> None:
    """The buffer is at-least-once, so the runner-scoped path needs the high-water mark too."""
    hub = build_hub(tmp_path)
    _register(hub)
    assert _report_local_pause(hub, paused=True, seq=1)["applied"] == [1]

    replay = _report_local_pause(hub, paused=True, seq=1)
    assert replay["applied"] == [] and replay["already_applied"] == [1]
    assert hub.client.get("/api/runners/runner-a").json()["locally_paused"] is True


def test_a_local_pause_from_an_unregistered_runner_is_kept(tmp_path: Path) -> None:
    """The buffer replays an outage in FIFO order, so a pause can precede its registration.

    Dropping it would lose the brake exactly when the board most needs it — so it lands,
    and the registration that follows finds it already there.
    """
    hub = build_hub(tmp_path)
    assert _report_local_pause(hub, paused=True, runner_id="runner-late")["applied"] == [1]

    hub.client.post("/api/runners", json={"runner_id": "runner-late", "workspace_id": "ws-a"})
    assert hub.client.get("/api/runners/runner-late").json()["locally_paused"] is True
