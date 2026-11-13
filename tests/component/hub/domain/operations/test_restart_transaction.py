"""Restart movement and gate arrival share one transaction."""

from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from blizzard.hub.domain.operations import restart as restart_module
from blizzard.hub.store.schema import chunk_restarts
from tests.support.scaffolding import build_hub, hub_scoped, ingest


def test_restart_rolls_back_movement_when_gate_arrival_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    graph = hub.services.graphs.get(chunk.graph_id)
    assert graph is not None
    before = hub.services.chunks.facts.load_facts(chunk_id)
    assert before is not None

    def fail(decisions: Any, facts: Any, transaction: Any, clock: Any, **kwargs: Any) -> tuple[int, ...]:
        landed = facts.load_facts(chunk_id, transaction=transaction)
        assert landed is not None
        assert len(landed.restarts) == len(before.restarts) + 1
        raise RuntimeError("gate failed")

    monkeypatch.setattr(restart_module, "open_graph_gate_on_arrival", fail)
    with pytest.raises(RuntimeError, match="gate failed"):
        hub.services.restart.restart(chunk, graph, node_name=None, by="operator")

    assert hub.services.chunks.facts.load_facts(chunk_id) == before
    with hub.engine.connect() as conn:
        assert conn.execute(hub_scoped(select(chunk_restarts.c.id))).all() == []
