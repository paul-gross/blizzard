"""``GET /api/chunks/{chunk_id}``'s ``neighborhood`` field (issue #462) — a chunk's
standing dependency edges one hop each way, proven end to end over HTTP.

Unlike ``blocked``, the field is always present: a chunk with no edges still reads back
two empty lists (D5)."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pytest

from blizzard.hub.domain.chunks.dependencies import IWriteChunkDependenciesRepository
from tests.support import HubHarness, build_hub, count_queries, ingest

pytestmark = pytest.mark.component

_DEPENDENT = {"source": "default", "ref": "dependent"}
_PREREQUISITE = {"source": "default", "ref": "prereq"}


def _declare(hub: HubHarness, dependent_id: str, prerequisite_id: str) -> None:
    resp = hub.client.post(f"/api/chunks/{dependent_id}/dependencies", json={"prerequisite_chunk_id": prerequisite_id})
    assert resp.status_code == 202, resp.text


def _release(hub: HubHarness, dependent_id: str, prerequisite_id: str) -> None:
    resp = hub.client.post(
        f"/api/chunks/{dependent_id}/dependencies/release", json={"prerequisite_chunk_id": prerequisite_id}
    )
    assert resp.status_code == 202, resp.text


def _detail(hub: HubHarness, chunk_id: str) -> dict:  # type: ignore[type-arg]
    resp = hub.client.get(f"/api/chunks/{chunk_id}")
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_a_chunk_with_no_edges_reads_two_empty_lists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [_DEPENDENT])

    assert _detail(hub, chunk_id)["neighborhood"] == {"prerequisites": [], "dependents": []}


def test_a_prerequisite_and_a_dependent_each_read_back_their_own_list(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    dependent_id = ingest(hub, [_DEPENDENT])
    _declare(hub, subject_id, prerequisite_id)
    _declare(hub, dependent_id, subject_id)

    neighborhood = _detail(hub, subject_id)["neighborhood"]

    assert neighborhood["prerequisites"] == [{"chunk_id": prerequisite_id, "status": "ready", "satisfied": False}]
    assert neighborhood["dependents"] == [{"chunk_id": dependent_id, "status": "ready", "satisfied": False}]


def test_a_satisfied_prerequisite_is_distinguished_from_an_unmet_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    done_id = ingest(hub, [{"source": "default", "ref": "done-prereq"}])
    pending_id = ingest(hub, [{"source": "default", "ref": "pending-prereq"}])
    _declare(hub, subject_id, done_id)
    _declare(hub, subject_id, pending_id)
    resp = hub.client.post(f"/api/chunks/{done_id}/complete", json={})
    assert resp.status_code == 202, resp.text

    prerequisites = {n["chunk_id"]: n for n in _detail(hub, subject_id)["neighborhood"]["prerequisites"]}

    assert prerequisites[done_id]["satisfied"] is True
    assert prerequisites[pending_id]["satisfied"] is False


def test_a_dependent_edge_is_satisfied_when_the_subject_itself_is_done(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    dependent_id = ingest(hub, [_DEPENDENT])
    _declare(hub, dependent_id, subject_id)
    resp = hub.client.post(f"/api/chunks/{subject_id}/complete", json={})
    assert resp.status_code == 202, resp.text

    dependents = _detail(hub, subject_id)["neighborhood"]["dependents"]

    assert dependents == [{"chunk_id": dependent_id, "status": "ready", "satisfied": True}]


def test_released_edges_are_absent_from_both_lists(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, subject_id, prerequisite_id)

    _release(hub, subject_id, prerequisite_id)

    assert _detail(hub, subject_id)["neighborhood"] == {"prerequisites": [], "dependents": []}


def test_a_neighbor_whose_facts_do_not_resolve_is_present_and_unsatisfied(tmp_path: Path) -> None:
    """D4: the residual race a delete's 409 refusal otherwise guards against — a standing
    edge naming a prerequisite id the fleet holds no facts for. Declared directly through
    the store, below the API's own existence check, to reach the case at all."""
    hub = build_hub(tmp_path)
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    dependencies = cast(IWriteChunkDependenciesRepository, hub.services.chunks.dependencies)
    dependencies.declare(subject_id, "chk_ghost", by="test", at=hub.clock.now())

    prerequisites = _detail(hub, subject_id)["neighborhood"]["prerequisites"]

    assert prerequisites == [{"chunk_id": "chk_ghost", "status": None, "satisfied": False}]


def test_a_chunk_with_no_standing_edges_costs_no_additional_facts_reads(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    bare_id = ingest(hub, [{"source": "default", "ref": "bare"}])
    subject_id = ingest(hub, [{"source": "default", "ref": "subject"}])
    prerequisite_id = ingest(hub, [_PREREQUISITE])
    _declare(hub, subject_id, prerequisite_id)

    def call(chunk_id: str) -> None:
        resp = hub.client.get(f"/api/chunks/{chunk_id}")
        assert resp.status_code == 200, resp.text

    bare_count = count_queries(hub.engine, lambda: call(bare_id))
    with_edge_count = count_queries(hub.engine, lambda: call(subject_id))

    assert bare_count < with_edge_count


def test_the_neighborhoods_facts_reads_are_bounded_by_its_own_edges_not_fleet_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    subject_id = ingest(small, [{"source": "default", "ref": "subject"}])
    prerequisite_id = ingest(small, [_PREREQUISITE])
    _declare(small, subject_id, prerequisite_id)

    large = build_hub(tmp_path / "large")
    large_subject_id = ingest(large, [{"source": "default", "ref": "subject"}])
    large_prerequisite_id = ingest(large, [_PREREQUISITE])
    _declare(large, large_subject_id, large_prerequisite_id)
    for i in range(8):  # 9x the small fleet, none of it named by the subject's own edges
        ingest(large, [{"source": "default", "ref": f"filler-{i}"}])

    def call(hub: HubHarness, chunk_id: str) -> None:
        resp = hub.client.get(f"/api/chunks/{chunk_id}")
        assert resp.status_code == 200, resp.text

    small_count = count_queries(small.engine, lambda: call(small, subject_id))
    large_count = count_queries(large.engine, lambda: call(large, large_subject_id))

    assert small_count == large_count
