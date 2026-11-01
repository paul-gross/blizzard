"""``POST /api/chunks`` choosing its project: named, inferred from the tokens' configured sources, or refused
whole with a ``422`` body naming what to choose; and a source unlinked under an ingest's feet."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from blizzard.hub.domain.chunk.ingest import IngestSourceNotLinked
from blizzard.hub.domain.chunk.model import WorkItemAuthor, WorkRef
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.wire.chunk import ChunkIngestConflict
from tests.support import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    default_project,
    hub_store_connections,
    link_project,
    seed_fake_work_sources,
    seed_graph,
    seed_project,
    seed_work_item,
)

pytestmark = pytest.mark.component


def two_projects(tmp_path: Path) -> tuple[HubHarness, ProjectId, ProjectId]:
    """A hub whose ``first`` project draws from ``a`` and ``shared``, and whose ``second`` draws from ``b`` and
    ``shared``; ``loose`` is linked to neither."""
    names = ("a", "b", "shared", "loose")
    hub = build_hub(tmp_path, work_sources={name: FakeWorkSource(name=name) for name in names}, link=False)
    seed_fake_work_sources(hub.engine, names, tmp_path=tmp_path, link=False)
    first = ProjectId(default_project(hub.engine))
    second = seed_project(hub.engine, "second")
    for project, sources in ((first, ("a", "shared")), (second, ("b", "shared"))):
        for source in sources:
            link_project(hub.engine, project=project, source=source)
    return hub, first, second


def ingest_project(hub: HubHarness, *tokens: str, project: str | None = None) -> tuple[int, dict[str, Any]]:
    body: dict[str, object] = {"tokens": list(tokens)}
    if project is not None:
        body["project"] = project
    resp = hub.client.post("/api/chunks", json=body)
    return resp.status_code, resp.json()


def _project_of(hub: HubHarness, chunk_id: str) -> ProjectId | None:
    chunk = hub.services.chunks.record.get(chunk_id)
    assert chunk is not None
    return chunk.project_id


def test_an_ingest_naming_no_project_mints_into_the_one_its_sources_link(tmp_path: Path) -> None:
    hub, first, second = two_projects(tmp_path)

    status, body = ingest_project(hub, "a:1")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == first
    status, body = ingest_project(hub, "b:1")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second


@pytest.mark.parametrize("tokens", [("shared:1",), ("a:2", "b:2")], ids=["shared-source", "sources-disagree"])
def test_an_ingest_its_sources_leave_open_answers_project_required_naming_the_candidates(
    tmp_path: Path, tokens: tuple[str, ...]
) -> None:
    hub, _, _ = two_projects(tmp_path)

    status, body = ingest_project(hub, *tokens)

    assert status == 422
    assert body["detail"]["code"] == "project_required"
    assert sorted(p["slug"] for p in body["detail"]["projects"]) == ["default", "second"]
    assert hub.client.get("/api/chunks").json()["chunks"] == []  # rejected whole


def test_a_named_project_takes_a_shared_source_by_slug_or_id(tmp_path: Path) -> None:
    hub, first, second = two_projects(tmp_path)

    status, body = ingest_project(hub, "shared:3", "b:3", project="second")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second
    status, body = ingest_project(hub, "shared:4", project=first)
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == first


def test_a_named_former_slug_still_names_its_project(tmp_path: Path) -> None:
    hub, _, second = two_projects(tmp_path)
    renamed = hub.client.patch("/api/projects/second", json={"slug": "beta"})
    assert renamed.status_code == 200, renamed.text

    status, body = ingest_project(hub, "shared:9", project="second")

    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second


def test_an_item_whose_holder_is_terminal_ingests_again_into_another_project(tmp_path: Path) -> None:
    hub, first, second = two_projects(tmp_path)
    status, body = ingest_project(hub, "shared:10", project="default")
    assert status == 201, body
    held = body["chunk_id"]
    assert _project_of(hub, held) == first
    stopped = hub.client.post(f"/api/chunks/{held}/stop", json={"by": "operator"})
    assert stopped.status_code == 202, stopped.text

    status, body = ingest_project(hub, "shared:10", project="second")

    assert status == 201, body
    assert body["chunk_id"] != held
    assert _project_of(hub, body["chunk_id"]) == second


def test_an_item_a_live_chunk_of_another_project_holds_answers_409_naming_its_holder(tmp_path: Path) -> None:
    """The live hold on an item spans the tenant: no project mints a second chunk over another's live one."""
    hub, first, _ = two_projects(tmp_path)
    status, body = ingest_project(hub, "shared:11", project="default")
    assert status == 201, body
    held = body["chunk_id"]
    assert _project_of(hub, held) == first

    status, body = ingest_project(hub, "shared:11", project="second")

    assert status == 409
    assert (
        body
        == ChunkIngestConflict(
            existing_chunk_id=held, source="shared", ref="11", slug="shared", label="shared#11"
        ).model_dump()
    )
    assert [c["chunk_id"] for c in hub.client.get("/api/chunks").json()["chunks"]] == [held]


def test_a_named_project_an_ingest_source_is_not_linked_to_answers_source_not_linked(tmp_path: Path) -> None:
    hub, _, second = two_projects(tmp_path)

    status, body = ingest_project(hub, "shared:5", "a:5", project="second")

    assert status == 422
    assert body["detail"]["code"] == "source_not_linked"
    assert body["detail"]["source"] == "a"
    assert body["detail"]["project"]["project_id"] == second
    assert body["detail"]["project"]["slug"] == "second"


def test_a_source_linked_to_no_project_answers_source_not_linked_with_a_null_project(tmp_path: Path) -> None:
    hub, _, _ = two_projects(tmp_path)

    status, body = ingest_project(hub, "loose:1")

    assert status == 422
    assert body["detail"]["code"] == "source_not_linked"
    assert body["detail"]["source"] == "loose"
    assert body["detail"]["project"] is None


def test_an_unknown_project_answers_422_before_any_token_resolves(tmp_path: Path) -> None:
    hub, _, _ = two_projects(tmp_path)

    status, body = ingest_project(hub, "nobody:1", project="nowhere")

    assert status == 422
    assert body["detail"][0]["type"] == "unknown_project"
    assert body["detail"][0]["loc"] == ["body", "project"]


def test_ingest_never_links_a_source(tmp_path: Path) -> None:
    hub, _, second = two_projects(tmp_path)

    ingest_project(hub, "a:6", project="second")

    assert hub.services.project_links.projects_linking_source("a") != [second]
    assert second not in hub.services.project_links.projects_linking_source("a")


def test_a_source_unlinked_after_the_project_was_chosen_refuses_the_ingest(tmp_path: Path) -> None:
    """An unlink landing between the project's choice and the mint refuses the ingest instead of minting a
    chunk the project no longer draws from."""
    hub, first, _ = two_projects(tmp_path)
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    ingest = hub.services.ingest
    chosen = ingest.choose_project([WorkRef(source="a", ref="7")], None)
    assert chosen == first
    unlinked = hub.client.post(f"/api/projects/{first}/source-links/a/retire")
    assert unlinked.status_code == 200, unlinked.text

    with pytest.raises(IngestSourceNotLinked) as refused:
        ingest.ingest([WorkRef(source="a", ref="7")], graph=graph, project=chosen)

    assert (refused.value.source, refused.value.project) == ("a", first)


def _hub_item_in(hub: HubHarness, project: ProjectId | None) -> str:
    """A hub item recorded in ``project`` whose resting chunk is stopped, so the item can be ingested again —
    its ``hub:<ref>`` token. ``None`` is a project the item records none of."""
    at = datetime(2026, 9, 2, tzinfo=UTC)
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_hub_item", at=at)
    item = seed_work_item(
        hub_store_connections(hub.engine),
        graph_id="gr_hub_item",
        author=WorkItemAuthor.user("u_1"),
        at=at,
        project_id=project,
    )
    stopped = hub.client.post(f"/api/chunks/ch_{item.ref}/stop", json={"by": "operator"})
    assert stopped.status_code == 202, stopped.text
    return f"hub:{item.ref}"


def test_a_named_project_refuses_a_hub_item_recorded_in_another(tmp_path: Path) -> None:
    hub, first, second = two_projects(tmp_path)
    token = _hub_item_in(hub, second)

    status, body = ingest_project(hub, token, project="default")

    assert status == 422
    assert token in body["detail"] and first in body["detail"] and second in body["detail"]


def test_a_hub_item_recorded_elsewhere_is_refused_beside_a_configured_source(tmp_path: Path) -> None:
    hub, first, second = two_projects(tmp_path)
    token = _hub_item_in(hub, second)

    status, body = ingest_project(hub, token, "a:1")

    assert status == 422
    assert token in body["detail"] and first in body["detail"] and second in body["detail"]
    assert [c for c in hub.client.get("/api/chunks").json()["chunks"] if c["status"] != "stopped"] == []


def test_a_hub_item_recorded_in_the_inferred_project_is_accepted_beside_a_configured_source(tmp_path: Path) -> None:
    hub, first, _ = two_projects(tmp_path)
    token = _hub_item_in(hub, first)

    status, body = ingest_project(hub, token, "a:1")

    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == first
