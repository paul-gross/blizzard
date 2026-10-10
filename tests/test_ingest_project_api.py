"""``POST /api/chunks`` choosing its project: named, inferred from the tokens' configured sources, or refused
whole with a ``422`` body naming what to choose; and a source unlinked under an ingest's feet."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub.domain.chunk.ingest import IngestSourceNotLinked
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.projects.model import ProjectId
from tests.support import (
    FakeWorkSource,
    HubHarness,
    build_hub,
    default_project,
    link_project,
    seed_fake_work_sources,
    seed_project,
)

pytestmark = pytest.mark.component


def _two_projects(tmp_path: Path) -> tuple[HubHarness, ProjectId, ProjectId]:
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


def _ingest(hub: HubHarness, *tokens: str, project: str | None = None) -> tuple[int, dict]:
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
    hub, first, second = _two_projects(tmp_path)

    status, body = _ingest(hub, "a:1")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == first
    status, body = _ingest(hub, "b:1")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second


@pytest.mark.parametrize("tokens", [("shared:1",), ("a:2", "b:2")], ids=["shared-source", "sources-disagree"])
def test_an_ingest_its_sources_leave_open_answers_project_required_naming_the_candidates(
    tmp_path: Path, tokens: tuple[str, ...]
) -> None:
    hub, _, _ = _two_projects(tmp_path)

    status, body = _ingest(hub, *tokens)

    assert status == 422
    assert body["detail"]["code"] == "project_required"
    assert sorted(p["slug"] for p in body["detail"]["projects"]) == ["default", "second"]
    assert hub.client.get("/api/chunks").json()["chunks"] == []  # rejected whole


def test_a_named_project_takes_a_shared_source_by_slug_or_id(tmp_path: Path) -> None:
    hub, first, second = _two_projects(tmp_path)

    status, body = _ingest(hub, "shared:3", "b:3", project="second")
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second
    status, body = _ingest(hub, "shared:4", project=first)
    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == first


def test_a_named_former_slug_still_names_its_project(tmp_path: Path) -> None:
    hub, _, second = _two_projects(tmp_path)
    renamed = hub.client.patch("/api/projects/second", json={"slug": "beta"})
    assert renamed.status_code == 200, renamed.text

    status, body = _ingest(hub, "shared:9", project="second")

    assert status == 201, body
    assert _project_of(hub, body["chunk_id"]) == second


def test_an_item_whose_holder_is_terminal_ingests_again_into_another_project(tmp_path: Path) -> None:
    hub, first, second = _two_projects(tmp_path)
    status, body = _ingest(hub, "shared:10", project="default")
    assert status == 201, body
    held = body["chunk_id"]
    assert _project_of(hub, held) == first
    stopped = hub.client.post(f"/api/chunks/{held}/stop", json={"by": "operator"})
    assert stopped.status_code == 202, stopped.text

    status, body = _ingest(hub, "shared:10", project="second")

    assert status == 201, body
    assert body["chunk_id"] != held
    assert _project_of(hub, body["chunk_id"]) == second


def test_a_named_project_an_ingest_source_is_not_linked_to_answers_source_not_linked(tmp_path: Path) -> None:
    hub, _, second = _two_projects(tmp_path)

    status, body = _ingest(hub, "shared:5", "a:5", project="second")

    assert status == 422
    assert body["detail"]["code"] == "source_not_linked"
    assert body["detail"]["source"] == "a"
    assert body["detail"]["project"]["project_id"] == second
    assert body["detail"]["project"]["slug"] == "second"


def test_a_source_linked_to_no_project_answers_source_not_linked_with_a_null_project(tmp_path: Path) -> None:
    hub, _, _ = _two_projects(tmp_path)

    status, body = _ingest(hub, "loose:1")

    assert status == 422
    assert body["detail"]["code"] == "source_not_linked"
    assert body["detail"]["source"] == "loose"
    assert body["detail"]["project"] is None


def test_an_unknown_project_answers_422_before_any_token_resolves(tmp_path: Path) -> None:
    hub, _, _ = _two_projects(tmp_path)

    status, body = _ingest(hub, "nobody:1", project="nowhere")

    assert status == 422
    assert body["detail"][0]["type"] == "unknown_project"
    assert body["detail"][0]["loc"] == ["body", "project"]


def test_ingest_never_links_a_source(tmp_path: Path) -> None:
    hub, _, second = _two_projects(tmp_path)

    _ingest(hub, "a:6", project="second")

    assert hub.services.project_links.projects_linking_source("a") != [second]
    assert second not in hub.services.project_links.projects_linking_source("a")


def test_a_source_unlinked_after_the_project_was_chosen_refuses_the_ingest(tmp_path: Path) -> None:
    """An unlink landing between the project's choice and the mint refuses the ingest instead of minting a
    chunk the project no longer draws from."""
    hub, first, _ = _two_projects(tmp_path)
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
