"""Questions and decisions carry their chunk's project: the store stamps it from the chunk row on both write paths,
the lists narrow by ``?project=`` (an id, a slug or a former slug; ``422`` for an unknown project; the whole tenant
without it), and every view names the project."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.hub.domain.chunk.model import Chunk, DecisionChoice
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.projects.model import ProjectId
from tests.support import (
    HubHarness,
    build_hub,
    chunk_stores,
    default_project,
    ingest,
    link_project,
    report_lease,
    report_question,
    seed_chunk_record,
    seed_graph,
    seed_project,
    seed_runner,
)

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_YAML = """
name: one-step
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        finish:
          description: Nothing left to review.
          to: done
"""


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path)


def _seed(hub: HubHarness) -> tuple[str, str]:
    """A chunk in each of two projects — ``default`` and ``gears`` — each with a gate and a question; returns the ids."""
    default = default_project(hub.engine)
    gears = seed_project(hub.engine, "gears")
    with hub.engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
    stores = chunk_stores(hub.engine, hub.clock)
    for chunk_id, project in (("ch_default", default), ("ch_gears", gears)):
        seed_chunk_record(
            stores,
            Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=_T0, project_id=ProjectId(project)),
        )
        stores.decisions.record_decision(
            imposed_by_runner_id=None,
            decision_id=f"dec_{chunk_id}",
            chunk_id=chunk_id,
            node_id="nd_1",
            node_name="n",
            epoch=1,
            choices=[DecisionChoice(name="ok", description="d")],
            at=_T0,
            artifacts=[],
            admission=EpochAdmission.AT_OR_ABOVE,
        )
        stores.questions.record_question(
            question_id=f"qn_{chunk_id}",
            chunk_id=chunk_id,
            node_id="nd_1",
            session_id=None,
            runner_id="r1",
            epoch=1,
            admission=EpochAdmission.AT_OR_ABOVE,
            question="which?",
            options=[],
            asked_at=_T0,
        )
    return default, gears


def _ids(hub: HubHarness, route: str, **params: str) -> list[str]:
    response = hub.client.get(f"/api/{route}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    rows = body["decisions"] if route == "decisions" else body
    return sorted(row["decision_id" if route == "decisions" else "question_id"] for row in rows)


def test_the_store_stamps_each_row_with_its_chunks_project(hub: HubHarness) -> None:
    default, gears = _seed(hub)
    stores = hub.services.chunks

    assert stores.questions.get_question("qn_ch_gears").project_id == gears  # type: ignore[union-attr]
    assert stores.decisions.get_decision("dec_ch_default").project_id == default  # type: ignore[union-attr]


def test_a_question_asked_through_the_fact_path_is_stamped_with_its_chunks_project(hub: HubHarness) -> None:
    gears = seed_project(hub.engine, "gears")
    link_project(hub.engine, project=gears, source="default")
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
    assert graph.status_code == 201, graph.text
    seed_runner(hub, "r1", projects=("gears",))
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}], project="gears")
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    )
    assert claim.status_code == 201, claim.text
    assert report_lease(hub, chunk_id, epoch=1, seq=1)["applied"] == [1]

    ack = report_question(hub, chunk_id, epoch=1, seq=2, question_id="qn_fact")

    assert ack["applied"] == [2], ack
    asked = hub.services.chunks.questions.get_question("qn_fact")
    assert asked is not None and asked.project_id == gears


def test_the_lists_narrow_by_project_id_slug_or_former_slug_and_read_the_whole_tenant_without_one(
    hub: HubHarness,
) -> None:
    default, gears = _seed(hub)
    renamed = hub.client.patch("/api/projects/gears", json={"slug": "cogs"})
    assert renamed.status_code == 200, renamed.text

    for route, whole, in_default, in_gears in (
        ("questions", ["qn_ch_default", "qn_ch_gears"], ["qn_ch_default"], ["qn_ch_gears"]),
        ("decisions", ["dec_ch_default", "dec_ch_gears"], ["dec_ch_default"], ["dec_ch_gears"]),
    ):
        assert _ids(hub, route) == whole
        assert _ids(hub, route, project=default) == in_default
        assert _ids(hub, route, project="default") == in_default
        assert _ids(hub, route, project=gears) == in_gears
        assert _ids(hub, route, project="cogs") == in_gears
        assert _ids(hub, route, project="gears") == in_gears


@pytest.mark.parametrize("route", ["questions", "decisions"])
def test_a_list_naming_an_unknown_project_answers_422(hub: HubHarness, route: str) -> None:
    _seed(hub)

    response = hub.client.get(f"/api/{route}", params={"project": "nope"})

    assert response.status_code == 422, response.text
    assert response.json()["detail"][0]["type"] == "unknown_project"


def test_every_view_names_the_chunks_project(hub: HubHarness) -> None:
    _, gears = _seed(hub)
    labels = {"project_id": gears, "project_slug": "gears", "project_name": "gears"}

    question = hub.client.get("/api/questions", params={"project": "gears"}).json()[0]
    decision = hub.client.get("/api/decisions", params={"project": "gears"}).json()["decisions"][0]
    detail = hub.client.get("/api/chunks/ch_gears").json()
    polled = hub.client.get("/api/fleet/questions/qn_ch_gears")

    assert {k: question[k] for k in labels} == labels
    assert {k: decision[k] for k in labels} == labels
    assert {k: detail["questions"][0][k] for k in labels} == labels
    assert {k: detail["decision"][k] for k in labels} == labels
    assert polled.status_code in (200, 401, 403), polled.text
