"""Delivery markers read back in durable write order (``artifacts.seq``), not by id —
two ids minted in one millisecond sort on their random suffix, not on write order."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead
from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from tests.support import build_hub, ingest

pytestmark = pytest.mark.component

# Same millisecond prefix; the later write gets the lexically smaller id.
_INVERTED_IDS = ("01K6DZZZZZZZZZZZZZZZZZZZZZ", "01K6DZZZZZ0000000000000000")


def test_same_millisecond_marker_writes_read_back_in_write_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], promote=True)
    minted: Iterator[str] = iter(_INVERTED_IDS)

    def inverted_mint(cls: type[Id], prefix: str, clock: IClock) -> Id:
        return cls(prefix, next(minted))

    monkeypatch.setattr(Id, "mint", classmethod(inverted_mint))
    at = hub.clock.now()
    writer = cast(IWriteChunkArtifactsRepository, hub.services.chunks.artifacts)
    for number in (3, 4):
        assert writer.record_hub_artifact(
            chunk_id,
            node_id="nd_deliver",
            node_name="deliver",
            epoch=1,
            name=f"delivery-pr/acme/one/{number}",
            content=f'{{"repo":"acme/one","number":{number},"url":"http://forge/acme/one/pull/{number}"}}',
            at=at,
            admission=EpochAdmission.AT_OR_ABOVE,
        )

    sources = cast(IReadChunkArtifactsRepository, hub.services.chunks.artifacts).delivery_sources_for([chunk_id])[
        chunk_id
    ]

    assert [m.name for m in sources.markers] == ["delivery-pr/acme/one/3", "delivery-pr/acme/one/4"]
    assert sources.markers[0].artifact_id > sources.markers[1].artifact_id  # ids alone would invert it
    view = DeliveryRead.of(ChunkFacts(minted=True), sources)
    assert [p.number for p in view.open_prs] == [4]
    assert [p.number for p in view.closed_prs] == [3]
    detail = hub.client.get(f"/api/chunks/{chunk_id}").json()
    assert [p["number"] for p in detail["open_prs"]] == [4]
