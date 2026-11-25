"""The runner's poll reads load only what they render (component tier): the answer poll probes for
a restart rather than loading facts, and the envelope reads the newest artifact per key."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from tests.support.chunk_questions import ask_question
from tests.support.scaffolding import HubHarness, build_hub, count_rows_read, hub_store_connections


def _superseded(hub: HubHarness) -> bool:
    resp = hub.client.get("/api/fleet/questions/qn_1")
    assert resp.status_code == 200, resp.text
    return resp.json()["superseded"]


def test_the_answer_poll_flips_on_a_restart_past_the_questions_epoch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ask_question(hub)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the answer poll loaded the chunk's facts")

    with monkeypatch.context() as patched:
        patched.setattr(ChunkFactsStore, "load_facts", refuse)
        assert _superseded(hub) is False

    restarted = hub.client.post(f"/api/chunks/{chunk_id}/restart", json={})
    assert restarted.status_code == 202, restarted.text

    with monkeypatch.context() as patched:
        patched.setattr(ChunkFactsStore, "load_facts", refuse)
        assert _superseded(hub) is True


def _deepen_history(hub: HubHarness, chunk_id: str, depth: int) -> None:
    conns = hub_store_connections(hub.engine)
    at = hub.clock.now()
    with conns.write("seed") as conn:
        for n in range(depth):
            conn.execute(
                conns.insert(s.chunk_restarts).values(
                    chunk_id=chunk_id, graph_id="gr", to_node_id="nd", epoch=1, restarted_by="op", recorded_at=at
                )
            )
            conn.execute(
                conns.insert(s.transitions).values(
                    transition_id=f"tr_{n}",
                    chunk_id=chunk_id,
                    from_node_id="nd",
                    to_node_id="nd",
                    graph_id="gr",
                    epoch=1,
                    choice_name=f"c{n}",
                    runner_id="r1",
                    recorded_at=at,
                )
            )
            conn.execute(
                conns.insert(s.usage_facts).values(
                    chunk_id=chunk_id,
                    node_id="nd",
                    epoch=1,
                    kind="spawn",
                    model="m",
                    input_tokens=1,
                    output_tokens=1,
                    cache_read_tokens=0,
                    cache_create_tokens=0,
                    cost_usd=0.1,
                    runner_id="r1",
                    recorded_at=at,
                )
            )


def test_the_answer_polls_rows_read_are_flat_in_fact_history_depth(tmp_path: Path) -> None:
    rows: list[int] = []
    for depth in (1, 3):
        (tmp_path / str(depth)).mkdir()
        hub = build_hub(tmp_path / str(depth))
        _deepen_history(hub, ask_question(hub), depth)
        rows.append(count_rows_read(hub.engine, lambda hub=hub: _superseded(hub)))

    assert rows[0] == rows[1]


def _seed_artifacts(hub: HubHarness, chunk_id: str, epochs: range) -> None:
    conns = hub_store_connections(hub.engine)
    with conns.write("seed") as conn:
        for epoch in epochs:
            for slot, name in enumerate(("spike", "notes")):
                conn.execute(
                    conns.insert(s.artifacts).values(
                        artifact_id=f"art_{epoch}_{slot}",
                        chunk_id=chunk_id,
                        node_id="nd_build",
                        node_name="build",
                        epoch=epoch,
                        name=name,
                        kind="asset",
                        data=f"{name} at {epoch}",
                        produced_at=hub.clock.now(),
                        seq=epoch * 10 + slot,
                    )
                )


def _envelope(hub: HubHarness, chunk_id: str) -> dict[str, Any]:
    resp = hub.client.get(f"/api/fleet/chunks/{chunk_id}/envelope")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_the_envelope_carries_and_reads_only_the_newest_epoch_of_each_key(tmp_path: Path) -> None:
    carried: list[list[dict[str, Any]]] = []
    rows: list[int] = []
    for first_epoch in (3, 1):
        (tmp_path / str(first_epoch)).mkdir()
        hub = build_hub(tmp_path / str(first_epoch))
        chunk_id = ask_question(hub)
        _seed_artifacts(hub, chunk_id, range(first_epoch, 4))
        carried.append(_envelope(hub, chunk_id)["artifacts"])
        rows.append(count_rows_read(hub.engine, lambda hub=hub, chunk_id=chunk_id: _envelope(hub, chunk_id)))

    assert sorted(a["content"] for a in carried[0]) == ["notes at 3", "spike at 3"]
    assert carried[0] == carried[1]
    assert rows[0] == rows[1]
