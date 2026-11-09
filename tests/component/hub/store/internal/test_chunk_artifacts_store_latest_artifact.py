"""``ChunkArtifactsStore.latest_artifact``'s ordering: a total order over ``(epoch, produced_at,
artifact_id)`` (``bzh:sql-portable``), so an exact tie — a designed-for state from a
crash-replay re-run — resolves deterministically, not on backend-dependent row order."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import sqlalchemy as sa

from blizzard.hub.domain.chunk.model import WorkRef, decide_close_enqueue
from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.store.schema import artifacts
from tests.support.scaffolding import HubHarness, build_hub, ingest, stamp


def _writable(hub: HubHarness) -> IWriteChunkArtifactsRepository:
    return cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)


def test_an_exact_epoch_and_produced_at_tie_resolves_to_the_same_artifact_every_time(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    at = hub.clock.now()

    # Distinct node_id, so both land: record_hub_artifact keys idempotency on
    # (chunk_id, node_id, epoch, name), and each mints its own id at the same instant.
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id="nd_a",
        node_name="survey",
        epoch=1,
        name="tied",
        content="one",
        at=at,
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([WorkRef(source="default", ref="1")], lambda _: True),
    )
    _writable(hub).record_hub_artifact(
        chunk_id,
        node_id="nd_b",
        node_name="survey",
        epoch=1,
        name="tied",
        content="two",
        at=at,
        admission=EpochAdmission.AT_OR_ABOVE,
        close_decision=decide_close_enqueue([WorkRef(source="default", ref="1")], lambda _: True),
    )

    winner = hub.services.chunks.artifacts.latest_artifact(chunk_id, "tied")
    assert winner is not None
    # Deterministic across repeated calls — not just "some" row each time.
    for _ in range(5):
        repeat = hub.services.chunks.artifacts.latest_artifact(chunk_id, "tied")
        assert repeat is not None
        assert repeat == winner


def test_an_exact_tie_resolves_to_the_highest_artifact_id_not_to_insertion_order(tmp_path: Path) -> None:
    """The tiebreaker is a *declared* order, not merely a repeatable one: on an exact
    tie the highest ``artifact_id`` wins. Seeded inverse to insertion order, so only the
    third ORDER BY term satisfies this — the backend's own row order does not."""
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    at = hub.clock.now()

    # `art_a...` first: the row a tie-blind query yields is the one the declared
    # order must *not* pick.
    with hub.engine.begin() as conn:
        for artifact_id, node_id in (
            ("art_aaaaaaaaaaaaaaaaaaaaaaaaaa", "nd_a"),
            ("art_zzzzzzzzzzzzzzzzzzzzzzzzzz", "nd_z"),
        ):
            conn.execute(
                sa.insert(artifacts).values(
                    stamp(
                        artifacts,
                        artifact_id=artifact_id,
                        chunk_id=chunk_id,
                        node_id=node_id,
                        node_name="survey",
                        epoch=1,
                        name="seeded-tie",
                        kind="asset",
                        data=node_id,
                        produced_at=at,
                        seq=1,
                    )
                )
            )

    winner = hub.services.chunks.artifacts.latest_artifact(chunk_id, "seeded-tie")

    assert winner is not None
    assert winner[0].artifact_id == "art_zzzzzzzzzzzzzzzzzzzzzzzzzz"


def test_latest_artifacts_matches_latest_artifact_per_name_and_drops_an_unknown_name(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    at = hub.clock.now()

    with hub.engine.begin() as conn:
        for artifact_id, node_id, epoch, name in (
            ("art_a1", "nd_a", 1, "multi"),
            ("art_a2", "nd_a", 2, "multi"),
            ("art_aa", "nd_a", 1, "tied"),
            ("art_zz", "nd_z", 1, "tied"),
            ("art_s1", "nd_a", 1, "single"),
        ):
            conn.execute(
                sa.insert(artifacts).values(
                    stamp(
                        artifacts,
                        artifact_id=artifact_id,
                        chunk_id=chunk_id,
                        node_id=node_id,
                        node_name="survey",
                        epoch=epoch,
                        name=name,
                        kind="asset",
                        data=artifact_id,
                        produced_at=at,
                        seq=1,
                    )
                )
            )

    repo = hub.services.chunks.artifacts
    names = ["multi", "tied", "single", "unknown"]
    result = repo.latest_artifacts(chunk_id, names)

    assert set(result) == {"multi", "tied", "single"}
    for name in ("multi", "tied", "single"):
        assert result[name] == repo.latest_artifact(chunk_id, name)
    assert result["multi"][0].artifact_id == "art_a2"
    assert result["tied"][0].artifact_id == "art_zz"
    assert repo.latest_artifacts(chunk_id, []) == {}


def test_producer_candidates_are_scoped_and_keep_only_each_producers_newest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    other = ingest(hub, [{"source": "default", "ref": "2"}], promote=True)
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        for chunk, producer, epoch in [(chunk_id, "alpha", n) for n in range(1, 30)] + [
            (chunk_id, "beta", 1),
            (other, "alpha", 99),
        ]:
            conn.execute(
                sa.insert(artifacts).values(
                    stamp(
                        artifacts,
                        artifact_id=f"art_{chunk}_{producer}_{epoch}",
                        chunk_id=chunk,
                        node_id=f"nd_{producer}",
                        node_name=producer,
                        epoch=epoch,
                        name="shared",
                        kind="asset",
                        data=str(epoch),
                        produced_at=at,
                        seq=epoch,
                    )
                )
            )
    repo = hub.services.chunks.artifacts
    candidates = repo.latest_artifact(chunk_id, "shared")
    assert [(a.node_name, a.epoch, a.produced_at) for a in candidates] == [("alpha", 29, at), ("beta", 1, at)]
    assert repo.latest_artifact(chunk_id, "shared", node_name="beta") == [candidates[1]]
    assert repo.latest_artifact(chunk_id, "shared", node_name="missing") == []
    assert repo.latest_artifacts(chunk_id, ["shared", "missing"], node_name="alpha") == {"shared": [candidates[0]]}


def test_candidate_reads_batch_names(tmp_path: Path, monkeypatch: object) -> None:
    from pytest import MonkeyPatch

    from blizzard.foundation.store import batching
    from tests.support.scaffolding import capture_statements

    assert isinstance(monkeypatch, MonkeyPatch)
    monkeypatch.setattr(batching, "BATCH_SIZE", 2)
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    with capture_statements(hub.engine) as statements:
        assert hub.services.chunks.artifacts.latest_artifacts(chunk_id, ["a", "b", "c", "d", "e"]) == {}
    assert len(statements) == 3


def test_candidate_competition_is_tenant_scoped(tmp_path: Path) -> None:
    from blizzard.hub.store import schema as s
    from blizzard.hub.store.internal.chunk_artifacts_store import ChunkArtifactsStore
    from blizzard.hub.tenancy import TenantId
    from tests.support.scaffolding import default_tenant, scoped_connections

    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    tenant = default_tenant(hub.engine)
    other = TenantId("ten_other")
    at = hub.clock.now()
    with hub.engine.begin() as conn:
        conn.execute(sa.insert(s.tenants).values(tenant_id=other, name="other", created_at=at, created_by="u"))
        for tid, epoch in [(tenant, 1), (other, 9)]:
            conn.execute(
                sa.insert(artifacts).values(
                    stamp(
                        artifacts,
                        tenant_id=tid,
                        artifact_id=f"art_{tid}",
                        chunk_id=chunk_id,
                        node_id="nd_1",
                        node_name="producer",
                        epoch=epoch,
                        name="shared",
                        kind="asset",
                        data=str(epoch),
                        produced_at=at,
                        seq=1,
                    )
                )
            )
    first = ChunkArtifactsStore(scoped_connections(hub.engine, tenant), hub.clock)
    second = ChunkArtifactsStore(scoped_connections(hub.engine, other), hub.clock)
    assert [a.epoch for a in first.latest_artifact(chunk_id, "shared")] == [1]
    assert [a.epoch for a in second.latest_artifact(chunk_id, "shared")] == [9]
