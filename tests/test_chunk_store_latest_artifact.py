"""``ChunkArtifactsStore.latest_artifact``'s ordering: a total order over ``(epoch, produced_at,
artifact_id)`` (``bzh:sql-portable``), so an exact tie — a designed-for state from a
crash-replay re-run — resolves deterministically, not on backend-dependent row order."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.chunks.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.store.schema import artifacts
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable(hub: HubHarness) -> IWriteChunkArtifactsRepository:
    return cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)


def test_an_exact_epoch_and_produced_at_tie_resolves_to_the_same_artifact_every_time(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    at = hub.clock.now()

    # Distinct node_id, so both land: record_hub_artifact keys idempotency on
    # (chunk_id, node_id, epoch, name), and each mints its own id at the same instant.
    _writable(hub).record_hub_artifact(
        chunk_id, node_id="nd_a", node_name="survey", epoch=1, name="tied", content="one", at=at
    )
    _writable(hub).record_hub_artifact(
        chunk_id, node_id="nd_b", node_name="survey", epoch=1, name="tied", content="two", at=at
    )

    winner = hub.services.chunks.artifacts.latest_artifact(chunk_id, "tied")
    assert winner is not None
    # Deterministic across repeated calls — not just "some" row each time.
    for _ in range(5):
        repeat = hub.services.chunks.artifacts.latest_artifact(chunk_id, "tied")
        assert repeat is not None
        assert repeat.artifact_id == winner.artifact_id


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
                    artifact_id=artifact_id,
                    chunk_id=chunk_id,
                    node_id=node_id,
                    node_name="survey",
                    epoch=1,
                    name="seeded-tie",
                    kind="asset",
                    data=node_id,
                    produced_at=at,
                )
            )

    winner = hub.services.chunks.artifacts.latest_artifact(chunk_id, "seeded-tie")

    assert winner is not None
    assert winner.artifact_id == "art_zzzzzzzzzzzzzzzzzzzzzzzzzz"
