"""``AnsweredFindingsReader`` over the real hub adapters (component tier): the
chunk-level read costs the same number of statements at one minted ref as at several,
and answers the union of the findings behind them."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_closure_store import insert_garden_proposal_closure_row
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    count_queries,
    default_project,
    hub_store_connections,
    seed_scope,
)

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


def _seed_minted(hub: HubHarness, n: int) -> list[WorkRef]:
    """``n`` accepted proposals, each minting `hub#<i>`; proposal ``i`` cites finding ``i``
    and finding ``0``, so every one overlaps on the first."""
    store = hub_store_connections(hub.engine)
    seed_scope(store, "blizzard", at=_NOW)
    findings, proposals = FindingStore(store), GardenProposalStore(store)
    pointers: list[WorkRef] = []
    for i in range(n):
        findings.add(
            f"fin_{i}",
            project_id=default_project(hub.engine),
            routine_name="nightly",
            scope_slug="blizzard",
            class_="stale-docstring",
            locus=f"a.py:{i}",
            summary="s",
            introduced=None,
            at=_NOW,
        )
        proposals.create(
            f"gprop_{i}",
            project_id=default_project(hub.engine),
            origin=GardenProposalOrigin.ROUTINE_RUN,
            routine_name="nightly",
            class_="c",
            title="t",
            body="b",
            findings=sorted({"fin_0", f"fin_{i}"}),
            at=_NOW,
        )
        pointer = WorkRef(source="hub", ref=str(i))
        pointers.append(pointer)
        with store.write("seed_mint") as conn:
            insert_garden_proposal_closure_row(
                conn,
                proposal_id=f"gprop_{i}",
                closure=GardenProposalClosureKind.ACCEPTED,
                reason=None,
                closed_by="u_1",
                at=_NOW,
                item_outcome=GardenProposalItemOutcome.MINTED,
                pointer=pointer,
            )
    return pointers


def _chunk(pointers: list[WorkRef]) -> Chunk:
    return Chunk(chunk_id="ch_1", graph_id="g_1", work_refs=pointers, minted_at=_NOW)


def test_statement_count_is_the_same_at_one_minted_ref_as_at_several(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    pointers = _seed_minted(hub, 4)
    reader = hub.services.answered_findings

    one = count_queries(hub.engine, lambda: reader.resolve_for_chunk(_chunk(pointers[:1])))
    several = count_queries(hub.engine, lambda: reader.resolve_for_chunk(_chunk(pointers)))

    assert one == several


def test_answers_the_union_once_each_and_nothing_for_an_unminted_chunk(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    pointers = _seed_minted(hub, 3)
    reader = hub.services.answered_findings

    answered = reader.resolve_for_chunk(_chunk(pointers))

    assert answered is not None
    assert [f.finding_id for f in answered] == ["fin_0", "fin_1", "fin_2"]
    assert reader.resolve_for_chunk(_chunk([WorkRef(source="hub", ref="unminted")])) is None
