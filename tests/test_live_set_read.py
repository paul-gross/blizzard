"""``bzh:live-set-read`` — a hot read excludes terminal chunks in the store query (component
tier).

The live read equals the full derivation minus its terminal statuses across every terminal
path, and every hot read's cost — statement count, rows fetched, response — stays flat when
only the terminal-chunk count grows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.foundation.chunk_status import TERMINAL_STATUSES
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from tests.support import (
    HubHarness,
    build_hub,
    capture_statements,
    chunk_stores,
    count_queries,
    count_rows_read,
    offending_index_scans,
    seed_chunk_record,
    seed_graph,
)
from tests.test_fleet_auth import _bearer, _enroll, _register

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_CAPABILITIES = [{"harness_id": "claude", "default": True}]


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _mint(hub: HubHarness, chunk_id: str, *, index: int, promote: bool = True) -> None:
    stores = chunk_stores(hub.engine, hub.clock)
    seed_chunk_record(
        stores, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_at(index), default_model=[])
    )
    if promote:
        stores.queue.record_promote(chunk_id, at=_at(index))


def _transition(engine: Engine, chunk_id: str, to_node: str, *, epoch: int, at: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(s.transitions).values(
                transition_id=f"tr_{chunk_id}_{epoch}_{to_node}",
                chunk_id=chunk_id,
                graph_id="gr_1",
                from_node_id=None,
                to_node_id=to_node,
                epoch=epoch,
                runner_id="r",
                recorded_at=_at(at),
            )
        )


def _terminate(hub: HubHarness, chunk_id: str, path: int) -> None:
    """Settle ``chunk_id`` by one of the terminal paths, each with several movement rows."""
    engine = hub.engine
    for epoch in (1, 2):
        _transition(engine, chunk_id, f"nd_{epoch}", epoch=epoch, at=epoch)
    kind = path % 3
    with engine.begin() as conn:
        if kind == 0:
            conn.execute(insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=_at(10)))
        elif kind == 1:
            conn.execute(insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=_at(10), completed_by="op"))
        else:
            conn.execute(insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=_at(10)))
            conn.execute(insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=_at(11), completed_by="op"))
    if path % 5 == 4:
        _transition(engine, chunk_id, RESERVED_TERMINAL, epoch=3, at=12)


def _done_by_transition(hub: HubHarness, chunk_id: str) -> None:
    _transition(hub.engine, chunk_id, "nd_1", epoch=1, at=1)
    _transition(hub.engine, chunk_id, RESERVED_TERMINAL, epoch=2, at=2)


def _held(hub: HubHarness, chunk_id: str, ref: str) -> None:
    """Mint ``chunk_id`` holding the hub-source pointer ``ref``."""
    stores = chunk_stores(hub.engine, hub.clock)
    seed_chunk_record(
        stores,
        Chunk(
            chunk_id=chunk_id,
            graph_id="gr_1",
            work_refs=[WorkRef(source="hub", ref=ref)],
            minted_at=_at(200),
            default_model=[],
        ),
    )


def _escalate(engine: Engine, chunk_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(s.escalations).values(chunk_id=chunk_id, epoch=1, takeover_command="resume", recorded_at=_at(5))
        )


def _add_terminal_only_history(hub: HubHarness) -> None:
    """Chunks that pass the terminal prefilter or hold a pointer yet can never be live:
    grouped away, deleted, escalated then stopped, and a pointer holder then completed."""
    for i in range(3):
        grouped, deleted = f"ch_grouped_{i}", f"ch_deleted_{i}"
        _held(hub, grouped, f"g{i}")
        _held(hub, deleted, f"d{i}")
        with hub.engine.begin() as conn:
            conn.execute(
                insert(s.chunk_grouped).values(chunk_id=grouped, grouped_into="ch_live_00", grouped_at=_at(300))
            )
            conn.execute(insert(s.chunk_deleted).values(chunk_id=deleted, deleted_at=_at(300), deleted_by="op"))
        escalated, held = f"ch_escalated_stopped_{i}", f"ch_held_done_{i}"
        _mint(hub, escalated, index=400 + i)
        _escalate(hub.engine, escalated)
        with hub.engine.begin() as conn:
            conn.execute(insert(s.chunk_stopped).values(chunk_id=escalated, stopped_at=_at(500)))
        _held(hub, held, f"h{i}")
        _done_by_transition(hub, held)


def _hub(tmp_path: Path, *, live: int, terminal: int) -> HubHarness:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        conn.execute(
            insert(s.graph_nodes).values(
                node_id="nd_1", graph_id="gr_1", name="nd_1", executor="runner", session="resume", judged_by="worker"
            )
        )
    for i in range(live):
        _mint(hub, f"ch_live_{i:02d}", index=i, promote=i % 4 != 3)  # a mix of ready and not_ready
    for i in range(terminal):
        chunk_id = f"ch_term_{i:03d}"
        _mint(hub, chunk_id, index=100 + i)
        if i % 5 == 4:
            _done_by_transition(hub, chunk_id)
        else:
            _terminate(hub, chunk_id, i)
    if terminal:
        _add_terminal_only_history(hub)
    return hub


def test_live_read_equals_the_full_derivation_minus_terminal_statuses(tmp_path: Path) -> None:
    hub = _hub(tmp_path, live=8, terminal=15)
    # A terminal transition a later movement superseded is live again, not terminal.
    _mint(hub, "ch_requeued", index=50)
    _transition(hub.engine, "ch_requeued", RESERVED_TERMINAL, epoch=1, at=1)
    _transition(hub.engine, "ch_requeued", "nd_2", epoch=2, at=2)
    # A tie with a later movement keeps the chunk in the prefilter; the derivation then decides.
    _mint(hub, "ch_tied", index=51)
    _transition(hub.engine, "ch_tied", RESERVED_TERMINAL, epoch=1, at=5)
    _transition(hub.engine, "ch_tied", "nd_2", epoch=1, at=5)

    facts = hub.services.chunks.facts
    expected = {cid: f.status() for cid, f in facts.load_all_facts().items() if f.status() not in TERMINAL_STATUSES}

    live = facts.load_live_statuses()
    assert live == expected
    assert "ch_requeued" in live
    assert not any(cid.startswith("ch_term_") for cid in live)


def _peek(hub: HubHarness, token: str) -> tuple[object, object]:
    get = hub.client.get("/api/fleet/queue/peek")
    post = hub.client.post("/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES}, headers=_bearer(token))
    assert get.status_code == post.status_code == 200
    return get.json(), post.json()


def _token(hub: HubHarness) -> str:
    _register(hub, runner_id="runner-a", workspace_id="ws-a")
    return _enroll(hub, "runner-a")


_READS = [
    "/api/queue",
    "/api/backlog",
    "/api/fleet/summary",
    "/api/events",
    "/api/chunks/ch_live_00/work-items",
    "/api/work-sources/hub/items",
]


def test_hot_reads_are_flat_in_statements_rows_and_response_across_terminal_count(tmp_path: Path) -> None:
    (tmp_path / "none").mkdir()
    (tmp_path / "many").mkdir()
    bare = _hub(tmp_path / "none", live=6, terminal=0)
    heavy = _hub(tmp_path / "many", live=6, terminal=15)
    bare_token, heavy_token = _token(bare), _token(heavy)

    assert _peek(bare, bare_token) == _peek(heavy, heavy_token)
    assert _peek(heavy, heavy_token)[0]["entries"]  # type: ignore[index]

    def post(hub: HubHarness, token: str):  # type: ignore[no-untyped-def]
        return lambda: hub.client.post(
            "/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES}, headers=_bearer(token)
        )

    reads = {
        "peek-post": (post(bare, bare_token), post(heavy, heavy_token)),
        "peek-get": (
            lambda: bare.client.get("/api/fleet/queue/peek"),
            lambda: heavy.client.get("/api/fleet/queue/peek"),
        ),
        **{path: ((lambda p=path: bare.client.get(p)), (lambda p=path: heavy.client.get(p))) for path in _READS},
    }
    for name, (on_bare, on_heavy) in reads.items():
        assert on_bare().json() == on_heavy().json(), name
        assert count_queries(bare.engine, on_bare) == count_queries(heavy.engine, on_heavy), name
        assert count_rows_read(bare.engine, on_bare) == count_rows_read(heavy.engine, on_heavy), name


def test_a_done_prerequisite_unblocks_its_dependent_and_a_stopped_one_does_not(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        conn.execute(
            insert(s.graph_nodes).values(
                node_id="nd_1", graph_id="gr_1", name="nd_1", executor="runner", session="resume", judged_by="worker"
            )
        )
    token = _token(hub)
    for i, chunk_id in enumerate(["ch_done", "ch_stopped", "ch_after_done", "ch_after_stopped"]):
        _mint(hub, chunk_id, index=i)
    _done_by_transition(hub, "ch_done")
    with hub.engine.begin() as conn:
        conn.execute(insert(s.chunk_stopped).values(chunk_id="ch_stopped", stopped_at=_at(5)))
        for dependent, prerequisite in [("ch_after_done", "ch_done"), ("ch_after_stopped", "ch_stopped")]:
            conn.execute(
                insert(s.chunk_dependencies).values(
                    dependency_id=f"dep_{dependent}",
                    dependent_chunk_id=dependent,
                    prerequisite_chunk_id=prerequisite,
                    declared_at=_at(6),
                    declared_by="op",
                )
            )

    entries = hub.client.get("/api/queue").json()["entries"]
    blocked = {e["chunk_id"]: e["blocked"] for e in entries}
    assert blocked["ch_after_done"] is None
    assert blocked["ch_after_stopped"] is not None

    matched = hub.client.post(
        "/api/fleet/queue/peek", json={"capabilities": _CAPABILITIES}, headers=_bearer(token)
    ).json()["entries"]
    assert [e["chunk_id"] for e in matched] == ["ch_after_done"]


def test_the_prefilter_and_candidate_reads_use_named_indexes(tmp_path: Path) -> None:
    hub = _hub(tmp_path, live=6, terminal=15)
    token = _token(hub)
    with capture_statements(hub.engine) as statements:
        _peek(hub, token)
    tables = {
        "chunk_stopped",
        "chunk_completed",
        "transitions",
        "chunk_migrations",
        "chunk_restarts",
        "queue_positions",
        "chunk_promoted",
        "chunk_dependencies",
    }
    assert offending_index_scans(hub.engine, statements, tables) == []
