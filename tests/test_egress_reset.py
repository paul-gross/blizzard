"""``POST /api/egress/reset`` and ``blizzard hub egress reset`` (component tier) — a dataset's cursor moved to an
instant, the moved window recorded, and the sweep's pass serialized with it."""

from __future__ import annotations

import threading
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from click.testing import CliRunner

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.cli import hub as hub_group
from blizzard.hub.domain.observability.egress.config import EgressConfig
from blizzard.hub.domain.observability.egress.repository import UsagePosition
from blizzard.hub.domain.observability.egress.reset import EgressReset
from blizzard.hub.store import schema
from tests.support import HubHarness, InMemoryEgressWriter
from tests.test_egress_sweep import _SETTLED, _closed_step, _cursor_rows, _hub, _rows, _store, _sweep

pytestmark = pytest.mark.component

_ENV = {"BZ_HUB_URL": "http://hub.local:8421"}


def _reset(hub: HubHarness, dataset: str, to: object) -> httpx.Response:
    return hub.client.post("/api/egress/reset", json={"dataset": dataset, "to": iso_utc(to)})  # type: ignore[arg-type]


def _events(hub: HubHarness) -> list[sa.Row]:  # type: ignore[type-arg]
    with hub.engine.connect() as conn:
        stmt = sa.select(schema.event_log).where(schema.event_log.c.kind == "egress-cursor-reset")
        return list(conn.execute(stmt.order_by(schema.event_log.c.id)).all())


def test_a_reset_back_appends_a_cursor_row_the_next_pass_reads_from_and_records_the_repeat(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    writer = InMemoryEgressWriter()
    _sweep(hub, writer).sweep()
    start = hub.clock.now()
    _closed_step(hub, graph, 1, usage=2)
    _sweep(hub, writer).sweep()
    assert len(_rows(writer, "invocations")) == 2
    before = _cursor_rows(hub)

    resp = _reset(hub, "invocations", start)

    assert resp.status_code == 200, resp.text
    assert resp.json()["direction"] == "repeated"
    assert resp.json()["to_at"] == iso_utc(start)
    assert _cursor_rows(hub) == before + 1
    moved = _store(hub).newest_cursor("invocations")
    assert moved is not None
    assert (moved.step, moved.usage, moved.row_count, moved.files) == (None, UsagePosition(start), 0, ())
    _sweep(hub, writer).sweep()
    assert [r["usage_id"] for r in _rows(writer, "invocations")] == [1, 2, 1, 2]
    [event] = _events(hub)
    assert event.severity == "info"
    assert '"direction": "repeated"' in event.detail
    assert f'"to": "{iso_utc(start)}"' in event.detail
    assert '"from": "' in event.detail


def test_a_reset_forward_skips_the_window_and_a_steps_reset_moves_both_positions(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    writer = InMemoryEgressWriter()
    _sweep(hub, writer).sweep()
    _closed_step(hub, graph, 1)
    hub.clock.advance(timedelta(seconds=60))
    ahead = hub.clock.now()

    resp = _reset(hub, "steps", ahead)

    assert resp.json()["direction"] == "skipped"
    moved = _store(hub).newest_cursor("steps")
    assert moved is not None
    assert moved.step is not None
    assert (moved.step.at, moved.usage) == (ahead, UsagePosition(ahead))
    _sweep(hub, writer).sweep()
    assert _rows(writer, "steps") == []


def test_the_sweep_skips_its_tick_while_a_reset_holds_the_pass_lock(tmp_path: Path) -> None:
    hub, graph = _hub(tmp_path, _SETTLED)
    writer = InMemoryEgressWriter()
    lock = threading.Lock()
    sweep = _sweep(hub, writer)
    sweep._pass_lock = lock
    sweep.sweep()
    _closed_step(hub, graph, 1)
    cursors = _cursor_rows(hub)

    with lock:
        sweep.sweep()

    assert writer.batches == []
    assert _cursor_rows(hub) == cursors
    sweep.sweep()
    assert len(_rows(writer, "steps")) == 1


def test_a_reset_waits_for_a_pass_in_flight(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path, _SETTLED)
    lock = threading.Lock()
    reset = EgressReset(
        egress=_store(hub),
        events=hub.services.event_log,
        clock=hub.clock,
        config=_SETTLED,
        active=True,
        pass_lock=lock,
    )
    _sweep(hub, InMemoryEgressWriter()).sweep()
    done = threading.Event()
    worker = threading.Thread(target=lambda: (reset.reset("invocations", hub.clock.now()), done.set()))
    with lock:
        worker.start()
        assert not done.wait(0.2)
    worker.join(timeout=5)
    assert done.is_set()


def test_a_reset_with_the_export_off_is_409(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    resp = _reset(hub, "steps", hub.clock.now())
    assert resp.status_code == 409
    assert "not configured" in resp.json()["detail"]
    assert _events(hub) == []


def test_an_unconfigured_dataset_and_a_future_instant_are_422(tmp_path: Path) -> None:
    config = EgressConfig(directory=tmp_path / "out", settle_seconds=0, datasets=("steps",))
    hub, _ = _hub(tmp_path, config)
    wrong = _reset(hub, "invocations", hub.clock.now())
    assert wrong.status_code == 422
    assert "invocations" in wrong.json()["detail"]
    future = _reset(hub, "steps", hub.clock.now() + timedelta(seconds=1))
    assert future.status_code == 422
    assert "future" in future.json()["detail"]
    assert _events(hub) == []
    assert _cursor_rows(hub) == 0


def test_the_cli_moves_the_cursor_and_renders_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub, _ = _hub(tmp_path, _SETTLED)
    _sweep(hub, InMemoryEgressWriter()).sweep()
    monkeypatch.setattr(
        httpx,
        "request",
        lambda method, url, *, json=None, params=None, timeout, **_: hub.client.request(method, url, json=json),
    )
    monkeypatch.setattr(
        httpx, "post", lambda url, *, json=None, params=None, timeout, **_: hub.client.post(url, json=json)
    )

    moved = CliRunner().invoke(
        hub_group, ["egress", "reset", "--dataset", "invocations", "--to", "2026-07-12T00:00:00"], env=_ENV
    )
    assert moved.exit_code == 0, moved.output
    assert "invocations cursor moved from" in moved.output
    assert "repeated" in moved.output

    refused = CliRunner().invoke(
        hub_group, ["egress", "reset", "--dataset", "nope", "--to", "2026-07-12T00:00:00"], env=_ENV
    )
    assert refused.exit_code != 0
    assert "not configured" in refused.output
