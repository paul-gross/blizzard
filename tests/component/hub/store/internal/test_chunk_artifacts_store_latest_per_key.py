"""``ChunkArtifactsStore.load_latest_artifacts`` returns the rows ``LatestArtifacts.of`` would keep
from the whole history, and reads only those (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.execution.envelope import LatestArtifacts
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections
from tests.support.scaffolding import (
    HubHarness,
    add_tenant,
    build_hub,
    count_rows_read,
    hub_store_connections,
    ingest,
    scoped_connections,
)


def _seed(hub: HubHarness, conns: ScopedConnections, chunk_id: str, *rows: tuple[str, str, str, int, int]) -> None:
    """Rows as ``(artifact_id, node_name, name, epoch, seq)``."""
    with conns.write("seed") as conn:
        for artifact_id, node_name, name, epoch, seq in rows:
            conn.execute(
                conns.insert(s.artifacts).values(
                    artifact_id=artifact_id,
                    chunk_id=chunk_id,
                    node_id=f"nd_{node_name}",
                    node_name=node_name,
                    epoch=epoch,
                    name=name,
                    kind="asset",
                    data=artifact_id,
                    produced_at=hub.clock.now(),
                    seq=seq,
                )
            )


def test_it_keeps_what_the_domain_fold_keeps_in_the_whole_reads_order(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    conns = hub_store_connections(hub.engine)
    chunk_id, neighbor = (ingest(hub, [{"source": "default", "ref": ref}]) for ref in ("1", "2"))
    _seed(
        hub,
        conns,
        chunk_id,
        ("art_p1", "plan", "doc", 1, 1),
        ("art_p3", "plan", "doc", 3, 7),
        ("art_p2", "plan", "doc", 2, 4),
        ("art_old", "plan", "only-old", 1, 2),
        ("art_b1", "build", "doc", 1, 3),
        # One key and epoch twice: the earlier seq wins, whatever the ids say.
        ("art_tie_z", "build", "tie", 2, 5),
        ("art_tie_a", "build", "tie", 2, 6),
        # An exact seq tie falls to the lower id.
        ("art_seq_b", "build", "same-seq", 2, 8),
        ("art_seq_a", "build", "same-seq", 2, 8),
    )
    _seed(hub, conns, neighbor, ("art_neighbor", "plan", "doc", 9, 1))
    artifacts = hub.services.chunks.artifacts
    other = add_tenant(hub.engine, "other")
    _seed(hub, scoped_connections(hub.engine, other), chunk_id, ("art_foreign", "plan", "doc", 9, 1))

    latest = artifacts.load_latest_artifacts(chunk_id)

    whole = artifacts.load_artifacts(chunk_id)
    kept = {row.artifact_id for row in LatestArtifacts.of(whole).rows}
    assert latest == [row for row in whole if row.artifact_id in kept]
    assert [row.artifact_id for row in latest] == ["art_old", "art_b1", "art_tie_z", "art_p3", "art_seq_a"]


def test_rows_read_grow_with_the_keys_not_the_history(tmp_path: Path) -> None:
    for depth in (1, 3):
        (tmp_path / str(depth)).mkdir()
        hub = build_hub(tmp_path / str(depth))
        chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
        _seed(
            hub,
            hub_store_connections(hub.engine),
            chunk_id,
            *(
                (f"art_{epoch}_{name}", "build", name, epoch, epoch * 10 + slot)
                for epoch in range(1, depth + 1)
                for slot, name in enumerate(("a", "b", "c"))
            ),
        )
        artifacts = hub.services.chunks.artifacts

        rows = count_rows_read(
            hub.engine, lambda artifacts=artifacts, chunk_id=chunk_id: artifacts.load_latest_artifacts(chunk_id)
        )

        assert rows == 3
