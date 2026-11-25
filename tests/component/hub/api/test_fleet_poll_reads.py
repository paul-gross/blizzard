"""The runner's poll reads load only what they render (component tier): the answer poll probes for
a restart rather than loading facts."""

from __future__ import annotations

from pathlib import Path

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
