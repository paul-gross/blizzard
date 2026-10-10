"""``POST /api/chunks/{id}/group`` across projects (component tier): a fold naming a chunk of
another project than the survivor's answers ``409`` and writes nothing; a fold within one
project of a two-project tenant folds as ever."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import HubHarness, build_hub, ingest, link_project, seed_project

pytestmark = pytest.mark.component


def _two_project_hub(tmp_path: Path) -> HubHarness:
    """A hub whose tenant holds ``default`` and ``beta``, both linked to the fake ``default`` source,
    so each ingest names its project."""
    hub = build_hub(tmp_path)
    link_project(hub.engine, project=seed_project(hub.engine, "beta"), source="default")
    return hub


def _refs(hub: HubHarness, chunk_id: str) -> set[str]:
    return {p["ref"] for p in hub.client.get(f"/api/chunks/{chunk_id}").json()["work_refs"]}


def test_grouping_a_chunk_of_another_project_answers_409_and_folds_nothing(tmp_path: Path) -> None:
    hub = _two_project_hub(tmp_path)
    survivor = ingest(hub, [{"source": "default", "ref": "1"}], project="default")
    same = ingest(hub, [{"source": "default", "ref": "2"}], project="default")
    other = ingest(hub, [{"source": "default", "ref": "3"}], project="beta")

    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [same, other]})

    assert resp.status_code == 409, resp.text
    assert other in resp.json()["detail"]
    assert _refs(hub, survivor) == {"1"}
    assert hub.client.get(f"/api/chunks/{same}").status_code == 200
    assert hub.client.get(f"/api/chunks/{other}").status_code == 200


def test_grouping_within_one_project_of_a_two_project_tenant_folds(tmp_path: Path) -> None:
    hub = _two_project_hub(tmp_path)
    survivor = ingest(hub, [{"source": "default", "ref": "1"}], project="beta")
    same = ingest(hub, [{"source": "default", "ref": "2"}], project="beta")
    ingest(hub, [{"source": "default", "ref": "3"}], project="default")

    resp = hub.client.post(f"/api/chunks/{survivor}/group", json={"merge_chunk_ids": [same]})

    assert resp.status_code == 200, resp.text
    assert _refs(hub, survivor) == {"1", "2"}
    assert hub.client.get(f"/api/chunks/{same}").status_code == 404
