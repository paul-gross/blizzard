"""The close-intent outbox's enqueue side (D1, blizzard#383): every ``ChunkStore``
transaction that lands or completes a chunk folds an intent per still-open work ref into
that same transaction — real ``ChunkStore``, real migrations."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.hub.domain.work import WorkItemCloseOutcome, WorkRef
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _land(hub: HubHarness, chunk_id: str, *, repo: str = "widget") -> None:
    """Simulate a generic hub command node's mid-run ``merged/<repo>`` marker."""
    hub.services.chunks.artifacts.record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name=f"merged/{repo}",
        content="sha",
        at=hub.clock.now(),
    )


def _pending_intents(hub: HubHarness) -> set[tuple[str, str, str]]:
    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.close_intents.c.chunk_id, s.close_intents.c.source, s.close_intents.c.ref).where(
                s.close_intents.c.retired_at.is_(None)
            )
        ).all()
    return {(r.chunk_id, r.source, r.ref) for r in rows}


def test_a_landing_marker_enqueues_one_intent_per_work_ref(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    _land(hub, chunk_id)

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}


def test_a_replayed_landing_marker_enqueues_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)

    _land(hub, chunk_id)
    hub.services.chunks.artifacts.record_hub_artifact(
        chunk_id,
        node_id="nd_deliver",
        node_name="deliver",
        epoch=1,
        name="merged/widget",
        content="sha",
        at=hub.clock.now(),
    )  # the same (node_id, epoch, name) — record_hub_artifact's own idempotency guard no-ops it

    with hub.engine.connect() as conn:
        rows = conn.execute(
            select(s.close_intents).where(
                (s.close_intents.c.chunk_id == chunk_id)
                & (s.close_intents.c.source == "default")
                & (s.close_intents.c.ref == "1")
            )
        ).all()
    assert len(rows) == 1


def test_a_stop_with_no_landing_enqueues_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.stop.stop(chunk, by="test")

    assert _pending_intents(hub) == set()


def test_operator_completion_enqueues_even_with_no_landed_repos(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.complete.complete(chunk, by="test")

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}


def test_a_grouped_chunks_landing_marker_enqueues_nothing(tmp_path: Path) -> None:
    survivor_id = ingest(hub := build_hub(tmp_path), [{"source": "default", "ref": "1"}], promote=False)
    merged_id = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)

    hub.services.group.group(survivor_id, [merged_id])
    _land(hub, merged_id)  # a landing signal reaching an already-ephemeral chunk

    assert _pending_intents(hub) == set()


def test_a_deleted_chunks_landing_marker_enqueues_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None

    hub.services.delete.delete(chunk, by="test")
    _land(hub, chunk_id)

    assert _pending_intents(hub) == set()


def test_a_ref_already_carrying_a_terminal_outcome_enqueues_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    hub.services.chunks.delivery.record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.CLOSED,
        reason=None,
        at=hub.clock.now(),
    )

    _land(hub, chunk_id)

    assert _pending_intents(hub) == set()


def test_a_ref_carrying_only_a_failed_outcome_still_enqueues(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    hub.services.chunks.delivery.record_work_item_closure(
        chunk_id,
        pointer=WorkRef(source="default", ref="1"),
        outcome=WorkItemCloseOutcome.FAILED,
        reason="boom",
        at=hub.clock.now(),
    )

    _land(hub, chunk_id)

    assert _pending_intents(hub) == {(chunk_id, "default", "1")}
