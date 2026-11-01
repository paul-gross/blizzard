"""Chunk reads under a project lens (component tier): ``?project=`` on ``GET /api/chunks``,
``/api/chunk-counts`` and ``/api/spend`` narrows each to one project's chunks, named by id, slug or
a slug the project left; an unknown project answers ``422``; with no ``?project=`` each read takes
the whole tenant. Every chunk-describing view names the chunk's project."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.projects.model import ProjectEdit, ProjectId
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from tests.support import (
    HubHarness,
    build_hub,
    count_queries,
    default_project,
    hub_store_connections,
    ingest,
    link_project,
    seed_project,
)

pytestmark = pytest.mark.component


def _two_project_hub(tmp_path: Path) -> tuple[HubHarness, ProjectId]:
    """A hub whose tenant holds ``default`` and ``beta``, both linked to the fake ``default`` source;
    returns it with ``beta``'s id."""
    hub = build_hub(tmp_path)
    beta = seed_project(hub.engine, "beta")
    link_project(hub.engine, project=beta, source="default")
    return hub, beta


def _ingest(hub: HubHarness, ref: str, project: str) -> str:
    chunk_id = ingest(hub, [{"source": "default", "ref": ref}], project=project)
    hub.clock.advance(timedelta(seconds=1))
    return chunk_id


def _listed(hub: HubHarness, **params: str) -> list[str]:
    resp = hub.client.get("/api/chunks", params=params)
    assert resp.status_code == 200, resp.text
    return [row["chunk_id"] for row in resp.json()["chunks"]]


def _record_usage(hub: HubHarness, chunk_id: str, tokens: int) -> None:
    ChunkUsageStore(hub_store_connections(hub.engine), hub.clock).record_usage(
        chunk_id,
        node_id="nd_build",
        epoch=1,
        runner_id="r1",
        kind="spawn",
        model="claude-opus-4-8",
        input_tokens=tokens,
        output_tokens=0,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=None,
        at=hub.clock.now(),
    )


def test_the_chunk_list_narrows_to_a_project_by_id_slug_or_former_slug_in_the_same_order(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    d1 = _ingest(hub, "1", "default")
    b1 = _ingest(hub, "2", "beta")
    d2 = _ingest(hub, "3", "default")
    b2 = _ingest(hub, "4", "beta")

    everything = _listed(hub)
    assert everything == [b2, d2, b1, d1]
    assert _listed(hub, project=beta) == [b2, b1]
    assert _listed(hub, project="beta") == [b2, b1]
    assert _listed(hub, project="default") == [d2, d1]

    [project] = [p for p in hub.services.projects.list_all() if p.project_id == beta]
    hub.services.project_authoring.edit(
        project, ProjectEdit(slug="gamma"), ChangeContext(actor="operator", door=Door.API)
    )
    assert _listed(hub, project="beta") == [b2, b1]
    assert _listed(hub, project="gamma") == [b2, b1]


def test_a_narrowed_list_pages_through_its_own_project_only(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    betas = [_ingest(hub, str(n), "beta" if n % 2 else "default") for n in range(1, 8)]
    expected = [chunk_id for n, chunk_id in zip(range(1, 8), betas, strict=True) if n % 2][::-1]

    seen: list[str] = []
    cursor: str | None = None
    while True:
        params = {"project": "beta", "limit": "2"} | ({"cursor": cursor} if cursor is not None else {})
        body = hub.client.get("/api/chunks", params=params).json()
        seen += [row["chunk_id"] for row in body["chunks"]]
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert seen == expected


@pytest.mark.parametrize("path", ["/api/chunks", "/api/chunk-counts", "/api/spend"])
def test_an_unknown_project_answers_422_naming_the_query_parameter(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path)
    params = {"project": "nowhere"} | ({"since": "2026-01-01T00:00:00Z"} if path == "/api/spend" else {})

    resp = hub.client.get(path, params=params)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"][0]["loc"] == ["query", "project"]


def test_chunk_counts_count_one_projects_chunks_and_the_whole_tenant_without_a_project(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    _ingest(hub, "1", "default")
    _ingest(hub, "2", "beta")
    ingest(hub, [{"source": "default", "ref": "3"}], project="beta", promote=False)

    whole = hub.client.get("/api/chunk-counts").json()
    narrowed = hub.client.get("/api/chunk-counts", params={"project": beta}).json()

    assert (whole["total"], whole["ready"], whole["not_ready"]) == (3, 2, 1)
    assert (narrowed["total"], narrowed["ready"], narrowed["not_ready"]) == (2, 1, 1)


def test_spend_sums_one_projects_usage_and_the_whole_tenants_without_a_project(tmp_path: Path) -> None:
    hub, _ = _two_project_hub(tmp_path)
    since = hub.clock.now().isoformat()
    _record_usage(hub, _ingest(hub, "1", "default"), 1)
    _record_usage(hub, _ingest(hub, "2", "beta"), 2)
    _record_usage(hub, _ingest(hub, "3", "beta"), 4)

    def spent(**params: str) -> int:
        resp = hub.client.get("/api/spend", params={"since": since} | params)
        assert resp.status_code == 200, resp.text
        return resp.json()["input_tokens"]

    assert (spent(), spent(project="beta"), spent(project="default")) == (7, 6, 1)


def test_every_chunk_describing_view_names_the_chunks_project(tmp_path: Path) -> None:
    hub, beta = _two_project_hub(tmp_path)
    resp = hub.client.post("/api/chunks", json={"tokens": ["default:1"], "project": "beta"})
    assert resp.status_code == 201, resp.text
    chunk_id = resp.json()["chunk_id"]
    labels = {"project_id": beta, "project_slug": "beta", "project_name": "beta"}

    def labels_of(body: dict[str, object]) -> dict[str, object]:
        return {key: body[key] for key in labels}

    assert labels_of(resp.json()) == labels
    assert labels_of(hub.client.get("/api/chunks").json()["chunks"][0]) == labels
    assert labels_of(hub.client.get(f"/api/chunks/{chunk_id}").json()) == labels
    [status] = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": [chunk_id]}).json()
    assert labels_of(status) == labels
    assert labels_of(hub.client.post(f"/api/chunks/{chunk_id}/promote").json()) == labels
    assert labels_of(hub.client.patch(f"/api/chunks/{chunk_id}", json={"default_effort": "high"}).json()) == labels


def test_the_chunk_list_reads_its_page_projects_once_however_many_projects_the_page_spans(tmp_path: Path) -> None:
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    one = build_hub(tmp_path / "one")
    for n in range(4):
        _ingest(one, str(n), default_project(one.engine))
    two, _ = _two_project_hub(tmp_path / "two")
    for n in range(4):
        _ingest(two, str(n), "beta" if n % 2 else "default")

    def listed(hub: HubHarness) -> None:
        assert len(_listed(hub)) == 4

    assert count_queries(one.engine, lambda: listed(one)) == count_queries(two.engine, lambda: listed(two))
