"""``ChunkStore.latest_artifact``'s ordering (blizzard#393 review, F4): a total order over
``(epoch, produced_at, artifact_id)`` (``bzh:sql-portable``), so an exact ``(epoch,
produced_at)`` tie — a real, designed-for state from a crash-replay re-run — still
resolves deterministically rather than on backend-dependent row order."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.work import IWriteChunkRepository
from tests.support import HubHarness, build_hub, ingest

pytestmark = pytest.mark.component


def _writable(hub: HubHarness) -> IWriteChunkRepository:
    return cast(IWriteChunkRepository, hub.services.chunks)


def test_an_exact_epoch_and_produced_at_tie_resolves_to_the_same_artifact_every_time(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    at = hub.clock.now()

    # Same chunk/name/epoch/produced_at, distinct node_id — record_hub_artifact's own
    # idempotency key is (chunk_id, node_id, epoch, name), so both inserts land, each
    # minting its own random-tailed artifact_id at the identical instant `at`.
    _writable(hub).record_hub_artifact(
        chunk_id, node_id="nd_a", node_name="survey", epoch=1, name="tied", content="one", at=at
    )
    _writable(hub).record_hub_artifact(
        chunk_id, node_id="nd_b", node_name="survey", epoch=1, name="tied", content="two", at=at
    )

    winner = hub.services.chunks.latest_artifact(chunk_id, "tied")
    assert winner is not None
    # Deterministic across repeated calls — not just "some" row each time.
    for _ in range(5):
        repeat = hub.services.chunks.latest_artifact(chunk_id, "tied")
        assert repeat is not None
        assert repeat.artifact_id == winner.artifact_id
