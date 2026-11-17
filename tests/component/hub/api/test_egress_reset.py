"""``POST /api/egress/reset`` and ``blizzard hub egress reset`` — a dataset's cursor moved to an
instant, the moved window recorded, and the sweep's pass serialized with it."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
import sqlalchemy as sa

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.repository import UsagePosition
from blizzard.hub.domain.observability.egress.reset import plan_reset
from tests.support.egress_world import (
    SETTLED,
    closed_step,
    egress_cursor_rows,
    egress_hub,
    egress_rows,
    egress_store,
    egress_sweep,
)
from tests.support.hub_event_rows import hub_event_rows
from tests.support.scaffolding import HubHarness, InMemoryEgressWriter
from tests.support.worker_http import bind_stubs

_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


def _reset(hub: HubHarness, dataset: str, to: datetime) -> httpx2.Response:
    return hub.client.post("/api/egress/reset", json={"dataset": dataset, "to": iso_utc(to)})


def _events(hub: HubHarness) -> list[sa.Row[Any]]:
    return hub_event_rows(hub.engine, "egress-cursor-reset")


def test_a_reset_back_appends_a_cursor_row_the_next_pass_reads_from_and_records_the_repeat(tmp_path: Path) -> None:
    hub, graph = egress_hub(tmp_path, SETTLED)
    writer = InMemoryEgressWriter()
    egress_sweep(hub, writer).sweep()
    start = hub.clock.now()
    closed_step(hub, graph, 1, usage=2)
    egress_sweep(hub, writer).sweep()
    assert len(egress_rows(writer, "invocations")) == 2
    before = egress_cursor_rows(hub)

    resp = _reset(hub, "invocations", start)

    assert resp.status_code == 200, resp.text
    assert resp.json()["direction"] == "repeated"
    assert resp.json()["to_at"] == iso_utc(start)
    assert egress_cursor_rows(hub) == before + 1
    moved = egress_store(hub).newest_cursor("invocations")
    assert moved is not None
    assert (moved.step, moved.usage, moved.row_count, moved.files) == (None, UsagePosition(start), 0, ())
    egress_sweep(hub, writer).sweep()
    assert [r["usage_id"] for r in egress_rows(writer, "invocations")] == [1, 2, 1, 2]
    [event] = _events(hub)
    assert event.severity == "info"
    assert '"direction": "repeated"' in event.detail
    assert f'"to": "{iso_utc(start)}"' in event.detail
    assert '"from": "' in event.detail


def test_a_reset_forward_skips_the_window_and_a_steps_reset_moves_both_positions(tmp_path: Path) -> None:
    hub, graph = egress_hub(tmp_path, SETTLED)
    writer = InMemoryEgressWriter()
    egress_sweep(hub, writer).sweep()
    closed_step(hub, graph, 1)
    hub.clock.advance(timedelta(seconds=60))
    ahead = hub.clock.now()

    resp = _reset(hub, "steps", ahead)

    assert resp.json()["direction"] == "skipped"
    moved = egress_store(hub).newest_cursor("steps")
    assert moved is not None
    assert moved.step is not None
    assert (moved.step.at, moved.usage) == (ahead, UsagePosition(ahead))
    egress_sweep(hub, writer).sweep()
    assert egress_rows(writer, "steps") == []


def test_stale_pass_cannot_overwrite_reset(tmp_path: Path) -> None:
    from dataclasses import replace

    hub, _ = egress_hub(tmp_path, SETTLED)
    store = egress_store(hub)
    egress_sweep(hub, InMemoryEgressWriter()).sweep()
    before = store.newest_cursor("invocations")
    assert before is not None
    moved_to = hub.clock.now() - timedelta(hours=1)
    result = _reset(hub, "invocations", moved_to)
    assert result.status_code == 200
    assert not store.append_cursor(replace(before, recorded_at=hub.clock.now()), after=before.cursor_id)
    current = store.newest_cursor("invocations")
    assert current is not None and current.position == moved_to


def test_reset_reports_actual_append_predecessor_not_newest_timestamp(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path, SETTLED)
    store = egress_store(hub)
    now = hub.clock.now()
    older_authority = now - timedelta(hours=3)
    predecessor = now - timedelta(hours=1)
    target = now - timedelta(hours=2)
    store.reset_cursor(plan_reset("invocations", older_authority, now=now))
    store.reset_cursor(plan_reset("invocations", predecessor, now=now - timedelta(minutes=1)))

    resp = _reset(hub, "invocations", target)

    assert resp.status_code == 200, resp.text
    assert resp.json()["from_at"] == iso_utc(predecessor)
    assert resp.json()["direction"] == "repeated"
    [event] = _events(hub)
    detail = json.loads(event.detail)
    assert detail["from"] == iso_utc(predecessor)
    assert detail["to"] == iso_utc(target)
    assert detail["direction"] == "repeated"


def test_a_reset_with_the_export_off_is_409(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path)
    resp = _reset(hub, "steps", hub.clock.now())
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"]
    assert _events(hub) == []


def test_an_unconfigured_dataset_and_a_future_instant_are_422(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "out", settle_seconds=0, datasets=("steps",))
    hub, _ = egress_hub(tmp_path, config)
    wrong = _reset(hub, "invocations", hub.clock.now())
    assert wrong.status_code == 422
    assert "invocations" in wrong.json()["detail"]
    future = _reset(hub, "steps", hub.clock.now() + timedelta(seconds=1))
    assert future.status_code == 422
    assert "future" in future.json()["detail"]
    assert _events(hub) == []
    assert egress_cursor_rows(hub) == 0


def test_the_cli_moves_the_cursor_and_renders_a_refusal(tmp_path: Path) -> None:
    hub, _ = egress_hub(tmp_path, SETTLED)
    egress_sweep(hub, InMemoryEgressWriter()).sweep()

    def _request(
        method: str, url: str, *, json: object = None, params: object = None, timeout: object, **_: object
    ) -> httpx2.Response:
        return hub.client.request(method, url, json=json)

    def _post(url: str, *, json: object = None, params: object = None, timeout: object, **_: object) -> httpx2.Response:
        return hub.client.post(url, json=json)

    bound = bind_stubs(request=_request, post=_post)

    moved = bound.runner.invoke(
        hub_group, ["egress", "reset", "--dataset", "invocations", "--to", "2026-07-12T00:00:00"], env=_ENV
    )
    assert moved.exit_code == 0, moved.output
    assert "invocations cursor moved from" in moved.output
    assert "repeated" in moved.output

    refused = bound.runner.invoke(
        hub_group, ["egress", "reset", "--dataset", "nope", "--to", "2026-07-12T00:00:00"], env=_ENV
    )
    assert refused.exit_code != 0
    assert "not configured" in refused.output


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-12T03:00:00Z", datetime(2026, 7, 12, 3, tzinfo=UTC)),
        ("2026-07-12T05:00:00+02:00", datetime(2026, 7, 12, 3, tzinfo=UTC)),
        ("2026-07-11T21:30:00-05:30", datetime(2026, 7, 12, 3, tzinfo=UTC)),
        ("2026-07-12", datetime(2026, 7, 12).astimezone(UTC)),
        ("2026-07-12T03:00:00", datetime(2026, 7, 12, 3).astimezone(UTC)),
        ("2026-07-12 03:00:00", datetime(2026, 7, 12, 3).astimezone(UTC)),
    ],
)
def test_reset_cli_sends_the_requested_instant_to_the_api(tmp_path: Path, value: str, expected: datetime) -> None:
    hub, _ = egress_hub(tmp_path, SETTLED)
    bodies: list[object] = []

    def post(url: str, *, json: object = None, timeout: object, **_: object) -> httpx2.Response:
        bodies.append(json)
        return hub.client.post(url, json=json)

    result = bind_stubs(post=post).runner.invoke(
        hub_group, ["egress", "reset", "--dataset", "invocations", "--to", value, "--json"], env=_ENV
    )

    assert result.exit_code == 0, result.output
    assert bodies == [{"dataset": "invocations", "to": iso_utc(expected)}]
    assert datetime.fromisoformat(json.loads(result.output)["to_at"]) == expected


@pytest.mark.parametrize("value", ["yesterday", "2026-13-01", "2026-07-12T03:00:00+25:00"])
def test_reset_cli_rejects_bad_times(value: str) -> None:
    result = bind_stubs().runner.invoke(hub_group, ["egress", "reset", "--dataset", "events", "--to", value], env=_ENV)

    assert result.exit_code == 2
    assert "Invalid value for '--to'" in result.output
