"""An ``escalation.recorded`` fact landing through ``FactIngestService.ingest`` (component tier): every escalation in a
push applies and derives ``needs_human``, none owes a close — so no close-skipped row or frame is written — and the push
reads no chunk record at all, however many escalations it carries."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

from blizzard.hub.api.ingest_broadcast import pushed_facts
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.wire.facts import RunnerFact, RunnerFactBatch
from tests.support.scaffolding import HubHarness, build_hub, emitted_events, hub_scoped, ingest

_RUNNER = "escalator"
_KIND = "work-item-close-skipped"


def _claimed(hub: HubHarness, ref: str) -> str:
    chunk_id = ingest(hub, [{"source": "default", "ref": ref}], promote=True)
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": _RUNNER, "workspace_id": "w", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    return chunk_id


def test_a_push_of_escalations_applies_each_owes_no_close_and_reads_no_chunk_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_ids = [_claimed(hub, str(i)) for i in range(3)]
    batch = RunnerFactBatch(
        facts=[
            RunnerFact(
                seq=seq,
                kind="escalation.recorded",
                payload={"chunk_id": chunk_id, "epoch": 1, "takeover_command": "cd wd && x"},
            )
            for seq, chunk_id in enumerate(chunk_ids, start=1)
        ]
    )
    reads: list[str] = []
    get, get_many = ChunkRecordStore.get, ChunkRecordStore.get_many

    def counted_get(self: ChunkRecordStore, *args: Any, **kwargs: Any) -> Any:
        reads.append("get")
        return get(self, *args, **kwargs)

    def counted_get_many(self: ChunkRecordStore, *args: Any, **kwargs: Any) -> Any:
        reads.append("get_many")
        return get_many(self, *args, **kwargs)

    since = hub.events.latest_id()
    monkeypatch.setattr(ChunkRecordStore, "get", counted_get)
    monkeypatch.setattr(ChunkRecordStore, "get_many", counted_get_many)
    result = hub.services.facts.ingest(_RUNNER, pushed_facts(batch))
    monkeypatch.undo()

    assert len(result.applied) == 3
    assert reads == []
    with hub.engine.connect() as conn:
        skipped = conn.execute(
            hub_scoped(select(func.count()).select_from(s.event_log).where(s.event_log.c.kind == _KIND))
        ).scalar_one()
        intents = conn.execute(hub_scoped(select(func.count()).select_from(s.close_intents))).scalar_one()
    assert (skipped, intents) == (0, 0)
    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "event-logged"]
    assert [f for f in frames if f["kind"] == _KIND] == []
    for chunk_id in chunk_ids:
        facts = hub.services.chunks.facts.load_facts(chunk_id)
        assert facts is not None
        assert facts.status() == ChunkStatus.NEEDS_HUMAN
