"""Completing a chunk whose work ref has no bound closer publishes its ``work-item-close-skipped`` frame from the
domain, once the completion's unit has committed (``bzh:publish-after-commit``): exactly one frame keyed to the
committed row, none when the unit rolls back, none on a replayed completion. Stores built with no services still record
the row and hand back its id — they simply have no one to publish it."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Connection, select

from blizzard.hub.domain.chunk.model import CloseEnqueueDecision, WorkRef
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_lifecycle_store
from tests.support.chunk_units import chunk_unit
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    chunk_stores,
    emitted_events,
    hub_scoped,
    hub_store_connections,
    ingest,
)

_KIND = "work-item-close-skipped"


def _skip_row_ids(hub: HubHarness) -> list[int]:
    with hub.engine.connect() as conn:
        return list(
            conn.execute(hub_scoped(select(s.event_log.c.id).where(s.event_log.c.kind == _KIND))).scalars().all()
        )


def _skip_frames(hub: HubHarness, *, since: int) -> list[dict[str, Any]]:
    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "event-logged"]
    return [frame for frame in frames if frame["kind"] == _KIND]


def test_completion_publishes_one_skip_frame_keyed_to_its_committed_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    visible_at_publish: list[list[int]] = []
    publish = hub.events.publish_event_logged

    def observe(**kwargs: Any) -> int:
        if kwargs["kind"] == _KIND:
            visible_at_publish.append(_skip_row_ids(hub))
        return publish(**kwargs)

    monkeypatch.setattr(hub.events, "publish_event_logged", observe)
    since = hub.events.latest_id()

    hub.services.complete.complete(chunk, by="test")

    rows = _skip_row_ids(hub)
    assert len(rows) == 1
    frames = _skip_frames(hub, since=since)
    assert [(f["key"], f["chunk_id"], f["project_id"]) for f in frames] == [
        (f"event_log:{rows[0]}", chunk_id, chunk.project_id)
    ]
    assert visible_at_publish == [rows]

    replayed_since = hub.events.latest_id()
    assert hub.services.complete.complete(chunk, by="test") is None
    assert _skip_frames(hub, since=replayed_since) == []
    assert _skip_row_ids(hub) == rows


def test_a_completion_unit_that_fails_after_the_enqueue_leaves_no_row_and_no_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    enqueue = chunk_lifecycle_store.enqueue_close_intents

    def fail_after_enqueue(
        conn: Connection, chunk_id: str, *, at: datetime, close_decision: CloseEnqueueDecision
    ) -> tuple[int, ...]:
        enqueue(conn, chunk_id, at=at, close_decision=close_decision)
        raise RuntimeError("rollback")

    monkeypatch.setattr(chunk_lifecycle_store, "enqueue_close_intents", fail_after_enqueue)
    since = hub.events.latest_id()

    with pytest.raises(RuntimeError, match="rollback"):
        hub.services.complete.complete(chunk, by="test")

    assert _skip_row_ids(hub) == []
    assert _skip_frames(hub, since=since) == []


def test_stores_with_no_services_record_the_skip_row_and_return_its_id(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    stores = chunk_stores(hub.engine, hub.clock)
    since = hub.events.latest_id()

    with chunk_unit(hub_store_connections(hub.engine), [chunk_id]) as tx:
        written = stores.lifecycle.record_completion(
            chunk_id,
            by="test",
            at=hub.clock.now(),
            close_decision=CloseEnqueueDecision(closable=frozenset(), skipped=frozenset({WorkRef("default", "1")})),
            waiting=(),
            transaction=tx,
        )

    assert written.close_skipped == tuple(_skip_row_ids(hub))
    assert len(written.close_skipped) == 1
    assert _skip_frames(hub, since=since) == []
