"""Which project an ingest mints into (unit tier): ``choose_ingest_project`` pinned by value, and the
mints that carry the project — ``mint_chunk``, ``Chunk.require_project`` and ``prepare_mint``. No
repository, no clock but a fixed one."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ingest import (
    IngestItemProjectMismatch,
    IngestProjectAmbiguous,
    IngestProjectUnknown,
    IngestSourceNotLinked,
    choose_ingest_project,
)
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkProjectMissing,
    IWriteWorkItemRepository,
    WorkRef,
    mint_chunk,
)
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.work_items.editing import prepare_mint
from tests.support import make_graph

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_ALPHA = ProjectId("proj_alpha")
_BETA = ProjectId("proj_beta")
_FORGE_1 = WorkRef(source="forge", ref="1")
_FORGE_2 = WorkRef(source="forge", ref="2")
_TRACKER_1 = WorkRef(source="tracker", ref="1")
_HUB_1 = WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref="1")
_HUB_2 = WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref="2")


def test_a_source_linked_to_one_project_alone_chooses_it() -> None:
    assert choose_ingest_project(None, [_FORGE_1, _FORGE_2], {"forge": [_ALPHA]}, {}) == _ALPHA


def test_sources_whose_single_project_agrees_choose_it() -> None:
    links = {"forge": [_ALPHA], "tracker": [_ALPHA]}

    assert choose_ingest_project(None, [_FORGE_1, _TRACKER_1], links, {}) == _ALPHA


def test_a_source_linked_to_several_projects_is_ambiguous_naming_them_by_id() -> None:
    with pytest.raises(IngestProjectAmbiguous) as exc_info:
        choose_ingest_project(None, [_FORGE_1], {"forge": [_BETA, _ALPHA]}, {})
    assert exc_info.value.candidates == (_ALPHA, _BETA)


def test_sources_whose_single_projects_differ_are_ambiguous_naming_both() -> None:
    with pytest.raises(IngestProjectAmbiguous) as exc_info:
        choose_ingest_project(None, [_TRACKER_1, _FORGE_1], {"forge": [_BETA], "tracker": [_ALPHA]}, {})
    assert exc_info.value.candidates == (_ALPHA, _BETA)


def test_one_source_linked_to_several_beside_one_linked_to_a_single_project_is_still_ambiguous() -> None:
    with pytest.raises(IngestProjectAmbiguous) as exc_info:
        choose_ingest_project(None, [_FORGE_1, _TRACKER_1], {"forge": [_ALPHA, _BETA], "tracker": [_ALPHA]}, {})
    assert exc_info.value.candidates == (_ALPHA, _BETA)


@pytest.mark.parametrize("links", [{}, {"forge": []}], ids=["absent", "empty"])
def test_a_source_linked_to_no_project_refuses_naming_it_and_no_project(links: dict[str, list[ProjectId]]) -> None:
    with pytest.raises(IngestSourceNotLinked) as exc_info:
        choose_ingest_project(None, [_FORGE_1], links, {})
    assert (exc_info.value.source, exc_info.value.project) == ("forge", None)


def test_an_unlinked_source_refuses_even_when_the_other_sources_agree() -> None:
    with pytest.raises(IngestSourceNotLinked) as exc_info:
        choose_ingest_project(None, [_FORGE_1, _TRACKER_1], {"forge": [_ALPHA]}, {})
    assert (exc_info.value.source, exc_info.value.project) == ("tracker", None)


def test_the_hub_source_sits_out_the_inference_beside_a_configured_source() -> None:
    chosen = choose_ingest_project(None, [_HUB_1, _FORGE_1], {"forge": [_ALPHA]}, {_HUB_1: _ALPHA})

    assert chosen == _ALPHA


def test_a_hub_item_recorded_elsewhere_is_refused_beside_a_configured_source() -> None:
    with pytest.raises(IngestItemProjectMismatch) as exc_info:
        choose_ingest_project(None, [_HUB_1, _FORGE_1], {"forge": [_ALPHA]}, {_HUB_1: _BETA})
    assert (exc_info.value.pointer, exc_info.value.recorded, exc_info.value.chosen) == (_HUB_1, _BETA, _ALPHA)


def test_a_hub_item_recording_no_project_is_accepted_beside_a_configured_source() -> None:
    assert choose_ingest_project(None, [_HUB_1, _FORGE_1], {"forge": [_ALPHA]}, {}) == _ALPHA


def test_hub_items_alone_follow_the_project_they_record_when_they_agree() -> None:
    assert choose_ingest_project(None, [_HUB_1, _HUB_2], {}, {_HUB_1: _BETA, _HUB_2: _BETA}) == _BETA


def test_hub_items_recording_different_projects_are_ambiguous() -> None:
    with pytest.raises(IngestProjectAmbiguous) as exc_info:
        choose_ingest_project(None, [_HUB_1, _HUB_2], {}, {_HUB_1: _BETA, _HUB_2: _ALPHA})
    assert exc_info.value.candidates == (_ALPHA, _BETA)


def test_a_hub_item_recording_no_project_leaves_nothing_to_infer() -> None:
    with pytest.raises(IngestProjectUnknown) as exc_info:
        choose_ingest_project(None, [_HUB_1, _HUB_2], {}, {_HUB_1: _ALPHA})
    assert exc_info.value.pointer == _HUB_2


def test_a_named_project_wins_over_the_links_that_would_infer_another() -> None:
    links = {"forge": [_ALPHA, _BETA]}

    assert choose_ingest_project(_BETA, [_FORGE_1], links, {}) == _BETA


def test_a_named_project_refuses_hub_items_recorded_elsewhere() -> None:
    with pytest.raises(IngestItemProjectMismatch) as exc_info:
        choose_ingest_project(_ALPHA, [_HUB_1], {}, {_HUB_1: _BETA})
    assert (exc_info.value.pointer, exc_info.value.recorded, exc_info.value.chosen) == (_HUB_1, _BETA, _ALPHA)


def test_a_named_project_takes_hub_items_recorded_there_or_nowhere() -> None:
    assert choose_ingest_project(_ALPHA, [_HUB_1, _HUB_2], {}, {_HUB_1: _ALPHA}) == _ALPHA


def test_a_named_project_refuses_the_first_source_not_linked_to_it() -> None:
    links = {"forge": [_ALPHA], "tracker": [_BETA]}

    with pytest.raises(IngestSourceNotLinked) as exc_info:
        choose_ingest_project(_ALPHA, [_FORGE_1, _TRACKER_1, _HUB_1], links, {})
    assert (exc_info.value.source, exc_info.value.project) == ("tracker", _ALPHA)


def test_a_minted_chunk_carries_the_project_it_names() -> None:
    chunk = mint_chunk([_FORGE_1], graph_id="gr_1", at=_T0, project_id=_ALPHA)

    assert (chunk.project_id, chunk.require_project()) == (_ALPHA, _ALPHA)


def test_a_chunk_built_without_a_project_refuses_to_name_one() -> None:
    chunk = Chunk(chunk_id="chk_bare", graph_id="gr_1", work_refs=[_FORGE_1], minted_at=_T0)

    with pytest.raises(ChunkProjectMissing) as exc_info:
        chunk.require_project()
    assert exc_info.value.chunk_id == "chk_bare"


class _Items:
    def allocate_ref(self, source: str) -> str:
        return "7"

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


class _NoHolders:
    def find_live_holder(self, pointer: WorkRef) -> str | None:
        return None

    def __getattr__(self, name: str) -> Any:
        raise NotImplementedError(f"should not touch {name!r}")


def test_prepare_mint_mints_the_allocated_pointer_into_the_project_it_names() -> None:
    pointer, chunk, at = prepare_mint(
        cast(IWriteWorkItemRepository, _Items()),
        cast(IReadChunkWorkRefsRepository, _NoHolders()),
        FixedClock(instant=_T0),
        RESERVED_HUB_SOURCE_NAME,
        graph=make_graph("gr_1", "default"),
        project_id=_BETA,
    )

    assert pointer == WorkRef(source=RESERVED_HUB_SOURCE_NAME, ref="7")
    assert (chunk.work_refs, chunk.project_id, at) == ([pointer], _BETA, _T0)
