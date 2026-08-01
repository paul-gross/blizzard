"""``external_subscription_usage.sampled`` facts land at the hub (issue #218, phase 3).

Drives :class:`~blizzard.hub.domain.facts.FactIngestService` directly against the real
store adapters (``ChunkStore``/``RunnerRegistryStore``) rather than through the raw
``RunnerRegistryStore.record_external_usage`` write, so the per-runner seq high-water
idempotency the wire contract promises is actually exercised — mirroring
``tests/test_activity_feed_store.py``'s real-internal-collaborators pattern. The
component tier (``POST /api/fleet/events`` through the real hub app) proves the
route-level broadcast; see ``tests/test_events.py``'s
``test_every_runner_changed_publish_site_names_its_kind`` for the frame shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.facts import FactIngestService
from blizzard.hub.domain.registry import FleetService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.wire.facts import EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED, RunnerFact, RunnerFactBatch
from tests.support import build_hub, emitted_events, migrate_to

pytestmark = pytest.mark.component

_T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _payload(*, sampled_at: datetime, utilization_pct: float) -> dict:
    return {
        "sampled_at": sampled_at.isoformat(),
        "windows": [
            {
                "window": "5h",
                "utilization_pct": utilization_pct,
                "resets_at": "2026-08-01T17:00:00+00:00",
                "window_seconds": 18000,
            }
        ],
    }


def _service(engine: sa.Engine, clock: FixedClock) -> FactIngestService:
    chunks = ChunkStore(engine, clock)
    fleet = FleetService(registry=RunnerRegistryStore(engine), clock=clock)
    return FactIngestService(chunks=chunks, fleet=fleet, clock=clock)


def _row(engine: sa.Engine, runner_id: str):  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return conn.execute(
            sa.select(s.runner_external_usage).where(s.runner_external_usage.c.runner_id == runner_id)
        ).one_or_none()


def test_applying_the_fact_upserts_one_row_and_a_later_call_wins(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    first = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(sampled_at=_T0, utilization_pct=10.0),
                )
            ],
        )
    )
    assert first.ack.applied == [1]

    row = _row(engine, "r1")
    assert row is not None
    assert json.loads(row.windows)[0]["utilization_pct"] == 10.0

    later = _T0.replace(hour=13)
    second = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=2,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(sampled_at=later, utilization_pct=55.0),
                )
            ],
        )
    )
    assert second.ack.applied == [2]

    # Exactly one row (upsert, not append), and it carries the later call's payload.
    with engine.connect() as conn:
        rows = conn.execute(sa.select(s.runner_external_usage).where(s.runner_external_usage.c.runner_id == "r1")).all()
    assert len(rows) == 1
    assert rows[0].sampled_at == later
    assert json.loads(rows[0].windows)[0]["utilization_pct"] == 55.0


def test_replayed_seq_at_or_below_high_water_is_already_applied_and_writes_nothing(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    first = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(sampled_at=_T0, utilization_pct=10.0),
                )
            ],
        )
    )
    assert first.ack.applied == [1]

    # Replay the same seq with a different payload — since it is at-or-below the
    # high-water mark, it must not apply (and must not overwrite the stored row).
    replay = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(sampled_at=_T0, utilization_pct=99.0),
                )
            ],
        )
    )
    assert replay.ack.applied == []
    assert replay.ack.already_applied == [1]

    row = _row(engine, "r1")
    assert row is not None
    assert json.loads(row.windows)[0]["utilization_pct"] == 10.0  # untouched by the replay


def test_fact_for_a_runner_with_no_registration_row_applies_without_stalling_high_water(tmp_path: Path) -> None:
    """Proves the deliberate no-FK decision (``runner_external_usage``'s schema
    comment): a fact for a runner the registry has never seen must not raise, must
    still land, and must not stall the seq high-water mark."""
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    result = service.ingest(
        RunnerFactBatch(
            runner_id="ghost-runner",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(sampled_at=_T0, utilization_pct=1.0),
                )
            ],
        )
    )
    assert result.ack.applied == [1]
    assert result.ack.high_water == 1

    row = _row(engine, "ghost-runner")
    assert row is not None


def test_posted_through_the_route_publishes_runner_changed_once_and_a_replay_publishes_nothing(
    tmp_path: Path,
) -> None:
    """The actual ``POST /api/fleet/events`` route, not ``_apply`` directly — a
    domain-level-only assertion would still pass with the ``hub/api/fleet.py`` branch
    missing (the fact would land but never broadcast)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    since = hub.events.latest_id()

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(sampled_at=_T0, utilization_pct=25.0),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [1]

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "runner-changed"]
    assert frames == [{"runner_id": "r1", "kind": "external-usage"}]

    replay_since = hub.events.latest_id()
    replay = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(sampled_at=_T0, utilization_pct=99.0),
                }
            ],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["already_applied"] == [1]

    replay_frames = [e for e in emitted_events(hub, since=replay_since) if e["event"] == "runner-changed"]
    assert replay_frames == []


def test_get_runners_renders_the_landed_sample_with_exact_wire_field_names(tmp_path: Path) -> None:
    """The read side (issue #218 phase 4): ``GET /api/runners`` off a live hub, after a
    fact batch carrying ``external_subscription_usage.sampled`` lands, renders the exact
    wire shape ``external_subscription_usage.{sampled_at, windows[].{window,
    utilization_pct, resets_at, window_seconds}}`` — the field names Phase 5's board
    consumes."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(sampled_at=_T0, utilization_pct=42.5),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    runners = hub.client.get("/api/runners").json()["runners"]
    assert len(runners) == 1
    usage = runners[0]["external_subscription_usage"]
    assert usage == {
        "sampled_at": "2026-08-01T12:00:00+00:00",
        "windows": [
            {
                "window": "5h",
                "utilization_pct": 42.5,
                "resets_at": "2026-08-01T17:00:00+00:00",
                "window_seconds": 18000,
            }
        ],
    }

    # Symmetric on the single-runner detail read too (`runner_view` is the one renderer).
    detail = hub.client.get("/api/runners/r1").json()
    assert detail["external_subscription_usage"] == usage
