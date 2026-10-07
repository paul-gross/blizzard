"""A seeded world of chunks across every terminal branch, shared by the component tests that
pin the SQL terminal predicates against :meth:`ChunkFacts.status`."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from sqlalchemy import insert

from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.store import schema as s
from tests.support import HubHarness, build_hub, chunk_stores, seed_chunk_record, seed_graph

OLD = timedelta(days=30)


def hub_with_graph(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=hub.clock.now() - timedelta(days=60))
    return hub


def mint(hub: HubHarness, chunk_id: str, *, ago: timedelta, promote: bool = True) -> None:
    at = hub.clock.now() - ago
    stores = chunk_stores(hub.engine, hub.clock)
    seed_chunk_record(stores, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=at, default_model=[]))
    if promote:
        stores.queue.record_promote(chunk_id, at=at)


def transition(hub: HubHarness, chunk_id: str, to_node: str, *, epoch: int, ago: timedelta, n: int = 0) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.transitions).values(
                transition_id=f"tr_{chunk_id}_{epoch}_{to_node}_{n}",
                chunk_id=chunk_id,
                graph_id="gr_1",
                from_node_id=None,
                to_node_id=to_node,
                epoch=epoch,
                runner_id="r",
                recorded_at=hub.clock.now() - ago,
            )
        )


def stop(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    with hub.engine.begin() as conn:
        conn.execute(insert(s.chunk_stopped).values(chunk_id=chunk_id, stopped_at=hub.clock.now() - ago))


def complete(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.chunk_completed).values(chunk_id=chunk_id, completed_at=hub.clock.now() - ago, completed_by="op")
        )


def done_by_transition(hub: HubHarness, chunk_id: str, *, ago: timedelta) -> None:
    mint(hub, chunk_id, ago=ago + timedelta(hours=1))
    transition(hub, chunk_id, "nd_1", epoch=1, ago=ago + timedelta(minutes=30))
    transition(hub, chunk_id, RESERVED_TERMINAL, epoch=2, ago=ago)


def every_branch(hub: HubHarness) -> None:
    """Each terminal branch, once finished long ago and once inside the window, plus live
    chunks and an ephemeral one."""
    for label, ago in (("old", OLD), ("new", timedelta(hours=1))):
        mint(hub, f"ch_stopped_{label}", ago=ago)
        stop(hub, f"ch_stopped_{label}", ago=ago)

        mint(hub, f"ch_stop_then_complete_{label}", ago=ago)
        stop(hub, f"ch_stop_then_complete_{label}", ago=ago)
        complete(hub, f"ch_stop_then_complete_{label}", ago=ago)  # a tie goes to the completion

        mint(hub, f"ch_complete_then_stop_{label}", ago=ago)
        complete(hub, f"ch_complete_then_stop_{label}", ago=ago + timedelta(minutes=1))
        stop(hub, f"ch_complete_then_stop_{label}", ago=ago)

        mint(hub, f"ch_operator_completed_{label}", ago=ago)
        complete(hub, f"ch_operator_completed_{label}", ago=ago)

        done_by_transition(hub, f"ch_done_transition_{label}", ago=ago)

        mint(hub, f"ch_restarted_{label}", ago=ago + timedelta(hours=1))
        transition(hub, f"ch_restarted_{label}", RESERVED_TERMINAL, epoch=1, ago=ago + timedelta(minutes=1))
        with hub.engine.begin() as conn:
            conn.execute(
                insert(s.chunk_restarts).values(
                    chunk_id=f"ch_restarted_{label}",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=2,
                    restarted_by="op",
                    recorded_at=hub.clock.now() - ago,
                )
            )

        mint(hub, f"ch_migrated_{label}", ago=ago + timedelta(hours=1))
        transition(hub, f"ch_migrated_{label}", RESERVED_TERMINAL, epoch=1, ago=ago)
        with hub.engine.begin() as conn:
            conn.execute(
                insert(s.chunk_migrations).values(
                    migration_id=f"mg_{label}",
                    chunk_id=f"ch_migrated_{label}",
                    from_graph_id="gr_1",
                    to_graph_id="gr_1",
                    landed_node_id="nd_1",
                    epoch=1,
                    recorded_at=hub.clock.now() - ago,  # a tie with the terminal transition
                )
            )

        # Two same-instant terminal transitions: every order of the tie derives done, so the
        # predicate settles it.
        mint(hub, f"ch_tie_{label}", ago=ago + timedelta(hours=1))
        transition(hub, f"ch_tie_{label}", RESERVED_TERMINAL, epoch=1, ago=ago, n=1)
        transition(hub, f"ch_tie_{label}", RESERVED_TERMINAL, epoch=1, ago=ago, n=2)

        # A terminal and a non-terminal transition at an identical instant and epoch: the
        # derivation orders the tie by fact order, so the predicate leaves the chunk live and the
        # handler's residue drop settles it.
        mint(hub, f"ch_mixed_tie_{label}", ago=ago + timedelta(hours=1))
        transition(hub, f"ch_mixed_tie_{label}", RESERVED_TERMINAL, epoch=1, ago=ago, n=1)
        transition(hub, f"ch_mixed_tie_{label}", "nd_1", epoch=1, ago=ago, n=2)

    mint(hub, "ch_ready", ago=OLD)
    mint(hub, "ch_not_ready", ago=OLD, promote=False)
    mint(hub, "ch_running", ago=OLD)
    transition(hub, "ch_running", "nd_1", epoch=1, ago=OLD)
    done_by_transition(hub, "ch_deleted_done", ago=OLD)
    mint(hub, "ch_deleted_live", ago=OLD)
    with hub.engine.begin() as conn:
        for chunk_id in ("ch_deleted_done", "ch_deleted_live"):
            conn.execute(insert(s.chunk_deleted).values(chunk_id=chunk_id, deleted_at=hub.clock.now(), deleted_by="op"))
