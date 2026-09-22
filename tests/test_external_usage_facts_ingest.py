"""``external_subscription_usage.sampled`` facts land at the hub (issue #218, phase 3).

Drives ``FactIngestService`` directly against the real store adapters, exercising the
per-runner seq high-water idempotency the wire contract promises. The component tier
(``POST /api/fleet/events`` through the real hub app) proves the route-level broadcast."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.event_log import EventLogService
from blizzard.hub.domain.facts import FactIngestService
from blizzard.hub.domain.registry import FleetService
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.wire.facts import (
    EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
    EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
    RunnerFact,
    RunnerFactBatch,
)
from tests.support import build_hub, chunk_stores, emitted_events, hub_store_connections, migrate_to

pytestmark = pytest.mark.component

_T0 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


def _payload(*, slug: str, sampled_at: datetime, utilization_pct: float, name: str | None = None) -> dict:
    payload: dict = {
        "slug": slug,
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
    if name is not None:
        payload["name"] = name
    return payload


def _service(engine: sa.Engine, clock: FixedClock) -> FactIngestService:
    store = hub_store_connections(engine)
    chunks = chunk_stores(engine, clock)
    fleet = FleetService(registry=RunnerRegistryStore(store), clock=clock)
    event_log = EventLogService(events=chunks.events, publisher=EventBroker())
    return FactIngestService(
        facts=chunks.facts,
        route=chunks.route,
        escalations=chunks.escalations,
        questions=chunks.questions,
        usage=chunks.usage,
        events=event_log,
        fleet=fleet,
        clock=clock,
    )


def _row(engine: sa.Engine, runner_id: str, slug: str = "anthropic"):  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return conn.execute(
            sa.select(s.runner_external_usage).where(
                s.runner_external_usage.c.runner_id == runner_id, s.runner_external_usage.c.slug == slug
            )
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
                    payload=_payload(slug="anthropic", sampled_at=_T0, utilization_pct=10.0),
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
                    payload=_payload(slug="anthropic", sampled_at=later, utilization_pct=55.0),
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
                    payload=_payload(slug="anthropic", sampled_at=_T0, utilization_pct=10.0),
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
                    payload=_payload(slug="anthropic", sampled_at=_T0, utilization_pct=99.0),
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
                    payload=_payload(slug="anthropic", sampled_at=_T0, utilization_pct=1.0),
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
                    "payload": _payload(slug="anthropic", sampled_at=_T0, utilization_pct=25.0),
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
                    "payload": _payload(slug="anthropic", sampled_at=_T0, utilization_pct=99.0),
                }
            ],
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["already_applied"] == [1]

    replay_frames = [e for e in emitted_events(hub, since=replay_since) if e["event"] == "runner-changed"]
    assert replay_frames == []


def test_get_runners_renders_the_landed_sample_on_its_subscription(tmp_path: Path) -> None:
    """``GET /api/runners`` renders the landed sample on its per-slug response entry."""
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
                    "payload": _payload(slug="anthropic", sampled_at=_T0, utilization_pct=42.5, name="Anthropic"),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    runners = hub.client.get("/api/runners").json()["runners"]
    assert len(runners) == 1
    subscriptions = {subscription["slug"]: subscription for subscription in runners[0]["subscriptions"]}
    assert subscriptions == {
        "anthropic": {
            "slug": "anthropic",
            "name": "Anthropic",
            "sampled_at": "2026-08-01T12:00:00+00:00",
            "windows": [
                {
                    "window": "5h",
                    "utilization_pct": 42.5,
                    "resets_at": "2026-08-01T17:00:00+00:00",
                    "window_seconds": 18000,
                }
            ],
            "condition": None,
        }
    }

    # Symmetric on the single-runner detail read too (`runner_view` is the one renderer).
    detail = hub.client.get("/api/runners/r1").json()
    assert detail["subscriptions"] == list(subscriptions.values())


def test_two_distinct_subscriptions_render_separately(tmp_path: Path) -> None:
    """Each slug's independently stored sample renders in ``subscriptions``."""
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
                    "payload": _payload(slug="anthropic", sampled_at=_T0, utilization_pct=42.5, name="Anthropic"),
                },
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(slug="openai", sampled_at=_T0, utilization_pct=17.0, name="OpenAI"),
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [1, 2]

    detail = hub.client.get("/api/runners/r1").json()

    subscriptions = {s["slug"]: s for s in detail["subscriptions"]}
    assert set(subscriptions) == {"anthropic", "openai"}
    assert subscriptions["anthropic"]["name"] == "Anthropic"
    assert subscriptions["anthropic"]["windows"][0]["utilization_pct"] == 42.5
    assert subscriptions["openai"]["name"] == "OpenAI"
    assert subscriptions["openai"]["windows"][0]["utilization_pct"] == 17.0


@pytest.mark.parametrize(
    "payload",
    [
        {"sampled_at": _T0.isoformat(), "windows": []},
        {"slug": "", "sampled_at": _T0.isoformat(), "windows": []},
        {"slug": 123, "sampled_at": _T0.isoformat(), "windows": []},
    ],
    ids=["missing-slug", "empty-slug", "non-string-slug"],
)
def test_a_fact_with_an_invalid_slug_is_rejected_without_writing_a_subscription(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    response = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": payload,
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["rejected"] == [1]
    assert hub.client.get("/api/runners/r1").json()["subscriptions"] == []


def test_malformed_usage_windows_are_omitted_and_a_later_valid_empty_sample_renders(tmp_path: Path) -> None:
    """A malformed window never poisons the registry read; the fact remains an advisory sample."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    valid_window = _payload(slug="anthropic", sampled_at=_T0, utilization_pct=42.5)["windows"][0]
    malformed = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "anthropic",
                        "name": "Anthropic",
                        "sampled_at": _T0.isoformat(),
                        "windows": [
                            valid_window,
                            {"window": "7d", "utilization_pct": 10.0},
                            {
                                "window": "1h",
                                "utilization_pct": "not-a-number",
                                "resets_at": "2026-08-01T13:00:00+00:00",
                                "window_seconds": 3600,
                            },
                            {
                                "window": "daily",
                                "utilization_pct": 20.0,
                                "resets_at": "not-an-instant",
                                "window_seconds": 86400,
                            },
                            {
                                "window": "zero",
                                "utilization_pct": 20.0,
                                "resets_at": "2026-08-01T12:00:00+00:00",
                                "window_seconds": 0,
                            },
                        ],
                    },
                }
            ],
        },
    )
    assert malformed.status_code == 200, malformed.text
    assert malformed.json()["applied"] == [1]

    first_read = hub.client.get("/api/runners")
    assert first_read.status_code == 200
    assert first_read.json()["runners"][0]["subscriptions"][0]["windows"] == [valid_window]

    empty_at = _T0 + timedelta(minutes=1)
    healthy = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": {
                        "slug": "anthropic",
                        "name": "Anthropic",
                        "sampled_at": empty_at.isoformat(),
                        "windows": [],
                    },
                }
            ],
        },
    )
    assert healthy.status_code == 200, healthy.text
    assert healthy.json()["applied"] == [2]
    assert hub.client.get("/api/runners").json()["runners"][0]["subscriptions"] == [
        {"slug": "anthropic", "name": "Anthropic", "sampled_at": empty_at.isoformat(), "windows": [], "condition": None}
    ]


def test_a_historical_malformed_window_cannot_break_runner_reads(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201
    assert (
        hub.client.post(
            "/api/fleet/events",
            json={
                "runner_id": "r1",
                "facts": [
                    {
                        "seq": 1,
                        "kind": "external_subscription_usage.sampled",
                        "payload": _payload(slug="anthropic", sampled_at=_T0, utilization_pct=42.5),
                    }
                ],
            },
        ).status_code
        == 200
    )
    with hub.engine.begin() as conn:
        conn.execute(
            s.runner_external_usage.update()
            .where(s.runner_external_usage.c.runner_id == "r1")
            .values(windows='[{"window":"5h"}]')
        )

    response = hub.client.get("/api/runners/r1")

    assert response.status_code == 200
    assert response.json()["subscriptions"][0]["windows"] == []


@pytest.mark.parametrize(
    "invalid_pct",
    [float("nan"), float("inf"), float("-inf"), -0.1, 100.1, True, "42"],
    ids=["nan", "inf", "-inf", "below", "above", "bool", "numeric-string"],
)
def test_non_finite_or_out_of_range_utilization_windows_are_omitted_at_ingest(
    tmp_path: Path, invalid_pct: object
) -> None:
    _, engine = migrate_to(tmp_path, "head")
    service = _service(engine, FixedClock(_T0))
    valid_window = _payload(slug="anthropic", sampled_at=_T0, utilization_pct=42.5)["windows"][0]
    result = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload={
                        "slug": "anthropic",
                        "sampled_at": _T0.isoformat(),
                        "windows": [
                            valid_window,
                            {
                                "window": "7d",
                                "utilization_pct": invalid_pct,
                                "resets_at": "2026-08-08T12:00:00+00:00",
                                "window_seconds": 604_800,
                            },
                        ],
                    },
                )
            ],
        )
    )

    assert result.ack.applied == [1]
    row = _row(engine, "r1")
    assert row is not None
    # Intake normalizes the instant, so the stored stamp carries an explicit +00:00
    # offset rather than the sender's own spelling of the same instant.
    assert json.loads(row.windows) == [{**valid_window, "resets_at": "2026-08-01T17:00:00+00:00"}]
    json.dumps(json.loads(row.windows), allow_nan=False)


def test_a_stale_subscription_does_not_blank_a_healthy_sibling_at_the_component_tier(tmp_path: Path) -> None:
    """One subscription's sample going stale must not blank a healthy sibling's
    (blizzard#436 phase 3's staleness-is-per-subscription acceptance bar), proven through
    the real HTTP read, not just the pure-domain unit tier."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    # Relative to the hub's own (fixed) clock, not `_T0` — staleness is judged against
    # `services.clock.now()` at read time, not the fact's own sampled instant.
    hub_now = hub.clock.now()
    healthy_at = hub_now - timedelta(minutes=1)
    stale_at = hub_now - timedelta(minutes=30)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(sampled_at=stale_at, utilization_pct=99.0, slug="openai", name="OpenAI"),
                },
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(sampled_at=healthy_at, utilization_pct=5.0, slug="anthropic", name="Anthropic"),
                },
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    detail = hub.client.get("/api/runners/r1").json()
    subscriptions = {s["slug"]: s for s in detail["subscriptions"]}

    # The stale sibling is simply absent — never a reason to omit the healthy one.
    assert set(subscriptions) == {"anthropic"}
    assert subscriptions["anthropic"]["windows"][0]["utilization_pct"] == 5.0


# blizzard#504 D7 — the `missed` fact.
# --------------------------------------------------------------------------- #


def _miss_payload(*, slug: str, missed_at: datetime, reason: str, name: str | None = None) -> dict:
    payload: dict = {"slug": slug, "missed_at": missed_at.isoformat(), "reason": reason}
    if name is not None:
        payload["name"] = name
    return payload


def _miss_row(engine: sa.Engine, runner_id: str, slug: str = "openai"):  # type: ignore[no-untyped-def]
    with engine.connect() as conn:
        return conn.execute(
            sa.select(s.runner_external_usage_misses).where(
                s.runner_external_usage_misses.c.runner_id == runner_id,
                s.runner_external_usage_misses.c.slug == slug,
            )
        ).one_or_none()


def test_a_missed_fact_upserts_one_row_and_a_later_call_wins(tmp_path: Path) -> None:
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    first = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
                    payload=_miss_payload(slug="openai", missed_at=_T0, reason="credential_lapsed", name="OpenAI"),
                )
            ],
        )
    )
    assert first.ack.applied == [1]

    row = _miss_row(engine, "r1")
    assert row is not None
    assert row.reason == "credential_lapsed"
    assert row.name == "OpenAI"

    later = _T0.replace(hour=13)
    second = service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=2,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
                    payload=_miss_payload(slug="openai", missed_at=later, reason="endpoint_unreachable"),
                )
            ],
        )
    )
    assert second.ack.applied == [2]

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(s.runner_external_usage_misses).where(s.runner_external_usage_misses.c.runner_id == "r1")
        ).all()
    # Exactly one row (upsert, not append), carrying the later call's payload.
    assert len(rows) == 1
    assert rows[0].reason == "endpoint_unreachable"
    assert rows[0].missed_at == later


def test_a_missed_fact_never_touches_the_sample_row(tmp_path: Path) -> None:
    """The sample row and the miss row are siblings (D7) — landing one never overwrites
    or deletes the other."""
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_SAMPLED,
                    payload=_payload(slug="openai", sampled_at=_T0, utilization_pct=10.0, name="OpenAI"),
                )
            ],
        )
    )
    service.ingest(
        RunnerFactBatch(
            runner_id="r1",
            facts=[
                RunnerFact(
                    seq=2,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
                    payload=_miss_payload(
                        slug="openai", missed_at=_T0 + timedelta(minutes=1), reason="credential_lapsed"
                    ),
                )
            ],
        )
    )

    sample_row = _row(engine, "r1", "openai")
    miss_row = _miss_row(engine, "r1", "openai")
    assert sample_row is not None
    assert miss_row is not None
    assert json.loads(sample_row.windows)[0]["utilization_pct"] == 10.0
    assert miss_row.reason == "credential_lapsed"


def test_a_missed_fact_for_a_runner_with_no_registration_row_applies_without_stalling_high_water(
    tmp_path: Path,
) -> None:
    """Mirrors the sampled fact's own no-FK, no-known-runner-required acceptance."""
    _, engine = migrate_to(tmp_path, "head")
    clock = FixedClock(_T0)
    service = _service(engine, clock)

    result = service.ingest(
        RunnerFactBatch(
            runner_id="ghost-runner",
            facts=[
                RunnerFact(
                    seq=1,
                    kind=EXTERNAL_SUBSCRIPTION_USAGE_MISSED,
                    payload=_miss_payload(slug="openai", missed_at=_T0, reason="credential_lapsed"),
                )
            ],
        )
    )
    assert result.ack.applied == [1]
    assert result.ack.high_water == 1
    assert _miss_row(engine, "ghost-runner") is not None


@pytest.mark.parametrize(
    "payload",
    [
        {"missed_at": _T0.isoformat(), "reason": "credential_lapsed"},
        {"slug": "", "missed_at": _T0.isoformat(), "reason": "credential_lapsed"},
        {"slug": 123, "missed_at": _T0.isoformat(), "reason": "credential_lapsed"},
    ],
    ids=["missing-slug", "empty-slug", "non-string-slug"],
)
def test_a_missed_fact_with_an_invalid_slug_is_rejected_without_writing_a_row(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    response = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [{"seq": 1, "kind": "external_subscription_usage.missed", "payload": payload}],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["rejected"] == [1]
    assert hub.client.get("/api/runners/r1").json()["subscriptions"] == []


def test_posted_missed_fact_publishes_runner_changed_once_and_a_replay_publishes_nothing(tmp_path: Path) -> None:
    """The actual route, mirroring the sampled fact's own broadcast pin — a real-side
    `_publish_one` branch missing would leave this landing but never broadcasting."""
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
                    "kind": "external_subscription_usage.missed",
                    "payload": _miss_payload(slug="openai", missed_at=_T0, reason="credential_lapsed"),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["applied"] == [1]

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "runner-changed"]
    assert frames == [{"runner_id": "r1", "kind": "external-usage"}]


def test_get_runners_renders_a_lapsed_credential_as_a_miss_only_condition_row(tmp_path: Path) -> None:
    """The hub end-to-end: a miss with no prior sample renders as a miss-only,
    `credential_lapsed` row on `GET /api/runners` (D7)."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    missed_at = hub.clock.now() - timedelta(minutes=1)
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": _miss_payload(
                        slug="openai", missed_at=missed_at, reason="credential_lapsed", name="OpenAI"
                    ),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    detail = hub.client.get("/api/runners/r1").json()
    assert detail["subscriptions"] == [
        {"slug": "openai", "name": "OpenAI", "sampled_at": None, "windows": [], "condition": "credential_lapsed"}
    ]


def test_get_runners_clears_the_condition_once_a_newer_sample_lands(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    now = hub.clock.now()
    missed_at = now - timedelta(minutes=10)
    sampled_at = now - timedelta(minutes=1)
    hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": _miss_payload(
                        slug="openai", missed_at=missed_at, reason="credential_lapsed", name="OpenAI"
                    ),
                }
            ],
        },
    )
    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 2,
                    "kind": "external_subscription_usage.sampled",
                    "payload": _payload(slug="openai", sampled_at=sampled_at, utilization_pct=5.0, name="OpenAI"),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    detail = hub.client.get("/api/runners/r1").json()
    assert len(detail["subscriptions"]) == 1
    assert detail["subscriptions"][0]["condition"] is None
    assert detail["subscriptions"][0]["sampled_at"] is not None
    assert detail["subscriptions"][0]["windows"][0]["utilization_pct"] == 5.0


def test_get_runners_stays_silent_for_a_non_lapsed_miss_reason(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/fleet/runners", json={"runner_id": "r1", "workspace_id": "w1"}).status_code == 201

    resp = hub.client.post(
        "/api/fleet/events",
        json={
            "runner_id": "r1",
            "facts": [
                {
                    "seq": 1,
                    "kind": "external_subscription_usage.missed",
                    "payload": _miss_payload(
                        slug="openai", missed_at=hub.clock.now(), reason="endpoint_unreachable", name="OpenAI"
                    ),
                }
            ],
        },
    )
    assert resp.status_code == 200, resp.text

    # Never sampled and only a non-lapsed miss on record — absent entirely, exactly as a
    # never-sampled slug always has been.
    assert hub.client.get("/api/runners/r1").json()["subscriptions"] == []
