"""The hub domain loops' set-keyed reads (component tier) — ``IWriteTranscriptSegments.epoch_owners``,
the batched sibling of ``epoch_owner`` (`bzh:bulk-reconstitution`), the locked handle's
``standing_edges_touching``, the retire pass's one record read, and the candidacy markers read
bounded by the visible-segment set.

The plural matches its singular per key and drops an unknown key, issues no statement for no
keys, stays correct across a lowered ``BATCH_SIZE``, and its statement count is equal at one key
and four. Per call site — ``retire``, transcript ``ingest`` — the read is issued once whether one
route or record is in play or four. The locked edge read and a one-chunk re-derive read a row count
that stays flat as other chunks' edges and derived segments grow."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store import batching as batching_module
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.fence import EpochOwner
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.execution.detach import DetachService
from blizzard.hub.domain.observability.analytics.derivation import ReDeriveScope
from blizzard.hub.domain.operations.delete import ChunkHasDependents, DeleteService
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import (
    HubHarness,
    build_hub,
    capture_statements,
    chunk_stores,
    count_queries,
    count_rows_read,
    hub_store_connections,
    migrate_to,
    pointer_token,
    seed_chunk_record,
    seed_graph,
    stamp,
)
from tests.test_analytics_events_api import _ingest_chunk, _record, _tool_turn

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_N = 4

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        pass:
          description: Complete and green.
          to: done
"""


def _chunk_engine(tmp_path: Path) -> tuple[ChunkStores, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    return chunk_stores(engine, FixedClock(_T0)), engine


def _dir(tmp_path: Path, n: int) -> Path:
    path = tmp_path / str(n)
    path.mkdir()
    return path


def _mint(chunks: ChunkStores, *chunk_ids: str) -> None:
    for chunk_id in chunk_ids:
        seed_chunk_record(chunks, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0))


# --- IWriteTranscriptSegments.epoch_owners ----------------------------------------


@pytest.fixture
def owners_engine(tmp_path: Path) -> Engine:
    """Chunks ``ch_0`` .. ``ch_4``, each owning epochs 1 and 2 — epoch 1 a runner's ``r{i}``,
    epoch 2 the hub's."""
    chunks, engine = _chunk_engine(tmp_path)
    _mint(chunks, *[f"ch_{i}" for i in range(5)])
    with engine.begin() as conn:
        for i in range(5):
            for epoch, runner_id in ((1, f"r{i}"), (2, None)):
                conn.execute(
                    sa.insert(s.epoch_owners).values(
                        stamp(s.epoch_owners, chunk_id=f"ch_{i}", epoch=epoch, runner_id=runner_id, recorded_at=_T0)
                    )
                )
    return engine


def test_epoch_owners_matches_epoch_owner_and_drops_an_unowned_lease(owners_engine: Engine) -> None:
    store = TranscriptSegmentStore(hub_store_connections(owners_engine))
    leases = [("ch_0", 1), ("ch_0", 2), ("ch_1", 1), ("ch_1", 3), ("ch_missing", 1)]

    owners = store.epoch_owners(leases)

    assert owners == {lease: owner for lease in leases if (owner := store.epoch_owner(*lease)) is not None}
    assert owners == {
        ("ch_0", 1): EpochOwner.runner("r0"),
        ("ch_0", 2): EpochOwner.hub(),
        ("ch_1", 1): EpochOwner.runner("r1"),
    }


def test_epoch_owners_keeps_only_the_named_pairs_of_a_batch(owners_engine: Engine) -> None:
    """Epochs narrow a batch as a set, so ``(ch_0, 2)`` matches the batch's epochs while naming
    no requested pair — it is filtered out, not answered."""
    store = TranscriptSegmentStore(hub_store_connections(owners_engine))

    assert store.epoch_owners([("ch_0", 1), ("ch_1", 2)]) == {
        ("ch_0", 1): EpochOwner.runner("r0"),
        ("ch_1", 2): EpochOwner.hub(),
    }


def test_epoch_owners_matches_across_a_batch_boundary(owners_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    store = TranscriptSegmentStore(hub_store_connections(owners_engine))
    leases = [(f"ch_{i}", 1) for i in range(5)]
    whole = store.epoch_owners(leases)

    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)

    assert store.epoch_owners(leases) == whole
    assert len(whole) == 5


def test_epoch_owners_is_statement_free_when_empty_and_flat_otherwise(owners_engine: Engine) -> None:
    store = TranscriptSegmentStore(hub_store_connections(owners_engine))

    assert count_queries(owners_engine, lambda: store.epoch_owners([])) == 0
    assert count_queries(owners_engine, lambda: store.epoch_owners([("ch_0", 1)])) == count_queries(
        owners_engine, lambda: store.epoch_owners([(f"ch_{i}", 1 + i % 2) for i in range(_N)])
    )


def _epoch_owner_statements(hub: HubHarness, records: list[dict]) -> int:
    with capture_statements(hub.engine) as statements:
        push = hub.client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": records})
    assert push.status_code == 200, push.text
    return sum(1 for statement, _ in statements if "FROM epoch_owners" in statement)


def test_transcript_ingest_reads_epoch_owners_once_across_record_counts(tmp_path: Path) -> None:
    counts = []
    for n in (1, _N):
        hub = build_hub(_dir(tmp_path, n))
        records = []
        for i in range(n):
            chunk_id = _ingest_chunk(hub, pointer_token=f"default:{i + 1}")
            record = _record(chunk_id, turns=[_tool_turn(0, "Read", {"file_path": "a.py"})], segment_id=f"sg_{i}")
            record["seq"] = i + 1
            records.append(record)
        counts.append(_epoch_owner_statements(hub, records))

    assert counts == [1, 1]


# --- ILockedChunkRead.standing_edges_touching -----------------------------------------


def _edged(tmp_path: Path, *, others: int) -> tuple[ChunkStores, Engine]:
    """``ch_t`` depending on ``ch_p`` and depended on by ``ch_d``, beside ``others`` unrelated
    standing edges ``ch_o{i}`` -> ``ch_q{i}``."""
    chunks, engine = _chunk_engine(tmp_path)
    _mint(chunks, "ch_t", "ch_p", "ch_d", *[f"ch_o{i}" for i in range(others)], *[f"ch_q{i}" for i in range(others)])
    pairs = [("ch_t", "ch_p"), ("ch_d", "ch_t"), *[(f"ch_o{i}", f"ch_q{i}") for i in range(others)]]
    for dependent, prerequisite in pairs:
        with chunks.exclusive.locked([dependent, prerequisite]) as handle:
            chunks.dependencies.declare_locked(handle, dependent, prerequisite, by="operator", at=_T0)
    return chunks, engine


def test_standing_edges_touching_answers_the_chunk_s_edges_in_either_role(tmp_path: Path) -> None:
    chunks, _ = _edged(tmp_path, others=2)

    with chunks.exclusive.locked(["ch_t"]) as handle:
        touching = handle.standing_edges_touching("ch_t")
        fleet = handle.standing_edges()

    assert touching == chunks.dependencies.standing_edges_for("ch_t")
    assert touching == [e for e in fleet if "ch_t" in (e.dependent_chunk_id, e.prerequisite_chunk_id)]
    assert {(e.dependent_chunk_id, e.prerequisite_chunk_id) for e in touching} == {("ch_t", "ch_p"), ("ch_d", "ch_t")}


def test_standing_edges_touching_reads_flat_rows_as_other_chunks_edges_grow(tmp_path: Path) -> None:
    rows, statements = [], []
    for others in (1, 6):
        chunks, engine = _edged(_dir(tmp_path, others), others=others)

        def read(chunks: ChunkStores = chunks) -> None:
            with chunks.exclusive.locked(["ch_t"]) as handle:
                handle.standing_edges_touching("ch_t")

        rows.append(count_rows_read(engine, read))
        statements.append(count_queries(engine, read))

    assert rows[0] == rows[1]
    assert statements[0] == statements[1]


def test_delete_still_refuses_a_standing_prerequisite_read_through_the_touching_edges(tmp_path: Path) -> None:
    chunks, engine = _edged(tmp_path, others=2)
    delete = DeleteService(
        items=WorkItemStore(hub_store_connections(engine)),
        clock=FixedClock(_T0),
        exclusive=chunks.exclusive,
        cycle_lock=threading.Lock(),
    )
    target = chunks.record.get("ch_t")
    assert target is not None

    with pytest.raises(ChunkHasDependents) as excinfo:
        delete.delete(target, by="operator")

    assert excinfo.value.dependent_chunk_ids == ["ch_d"]


# --- FleetService.retire ----------------------------------------------------------


def _holding_hub(tmp_path: Path, n: int) -> HubHarness:
    """A hub where ``runner-a`` holds ``n`` claimed chunks."""
    hub = build_hub(tmp_path)
    assert (
        hub.client.post(
            "/api/fleet/runners",
            json={
                "runner_id": "runner-a",
                "workspace_id": "ws-a",
                "capabilities": [{"harness_id": "claude", "default": True}],
            },
        ).status_code
        == 201
    )
    assert hub.client.post("/api/graphs", json={"definition_yaml": _YAML}).status_code == 201
    for i in range(n):
        token = pointer_token({"source": "default", "ref": str(700 + i)})
        chunk_id = hub.client.post("/api/chunks", json={"tokens": [token]}).json()["chunk_id"]
        assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
        claim = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": "runner-a", "workspace_id": "ws-a", "environment_ids": ["e1"]},
        )
        assert claim.status_code == 201, claim.text
    return hub


def test_retire_reads_its_routes_records_once_across_route_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each route's release keeps its own lock and transaction; with it stubbed out, what is left
    of the pass — the holdings, the routes and their records — is flat across route counts."""
    released: list[str] = []
    counts = []
    for n in (1, _N):
        hub = _holding_hub(_dir(tmp_path, n), n)
        registration = hub.services.registry.get_runner("runner-a")
        assert registration is not None
        monkeypatch.setattr(
            DetachService, "release_held", lambda self, chunk, *, runner_id: released.append(chunk.chunk_id)
        )
        counts.append(
            count_queries(
                hub.engine,
                lambda hub=hub, registration=registration: hub.services.fleet.retire(registration, by="op", force=True),
            )
        )
        monkeypatch.undo()

    assert counts[0] == counts[1]
    assert len(released) == 1 + _N


def test_a_forced_retire_still_releases_every_held_route(tmp_path: Path) -> None:
    hub = _holding_hub(tmp_path, _N)

    resp = hub.client.post("/api/runners/runner-a/retire", json={"by": "op", "force": True})

    assert resp.status_code == 200, resp.text
    assert len(resp.json()["released_chunk_ids"]) == _N


# --- TranscriptEventStore.candidacy -----------------------------------------------


def _derived_hub(tmp_path: Path, *, others: int) -> tuple[HubHarness, str]:
    """A hub whose target chunk and ``others`` further chunks each carry one segment, every
    segment derived and its marker current."""
    hub = build_hub(tmp_path)
    turns = [_tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z")]
    records = []
    chunk_ids = []
    for i in range(others + 1):
        chunk_id = _ingest_chunk(hub, pointer_token=f"default:{i + 1}")
        chunk_ids.append(chunk_id)
        record = _record(chunk_id, turns=turns, segment_id=f"sg_{i}")
        record["seq"] = i + 1
        records.append(record)
    push = hub.client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": records})
    assert push.status_code == 200, push.text
    for i in range(others + 1):
        hub.services.event_derivation_service.re_derive(ReDeriveScope.of(segment_id=f"sg_{i}", chunk_id=None), limit=1)
    return hub, chunk_ids[0]


def test_a_one_chunk_re_derive_reads_flat_rows_as_other_chunks_derived_segments_grow(tmp_path: Path) -> None:
    rows = []
    for others in (1, 6):
        hub, chunk_id = _derived_hub(_dir(tmp_path, others), others=others)
        derive = hub.services.event_derivation_service
        rows.append(
            count_rows_read(
                hub.engine,
                lambda derive=derive, chunk_id=chunk_id: derive.re_derive(
                    ReDeriveScope.of(segment_id=None, chunk_id=chunk_id), limit=10
                ),
            )
        )

    assert rows[0] == rows[1]
