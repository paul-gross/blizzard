"""The garden family's refusals (component tier): each answers ``application/problem+json`` with its own type and
status, carrying the same ``detail`` it carried before it was typed, and is declared by its operation."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from httpx2 import Response
from pydantic import BaseModel

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.api import openapi_problems, problem_declarations
from blizzard.hub.api.problem_declarations_garden import GARDEN_DECLARATIONS
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.scopes import ScopeSlug, ScopeSlugError
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.wire import problem_types_admin as admin_types
from blizzard.wire import problem_types_garden as types
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.problem import REVISION_CONFLICT
from tests.support.refusal_observation import declared_refusal_mismatches
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_project,
    hub_store_connections,
    project_record,
    stamp,
)

_CTX = ChangeContext(actor="operator", door=Door.API)
_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[HubHarness]:
    # The registry carries the family's declarations; unioning them again here is idempotent.
    union = (*problem_declarations.HUB_PROBLEM_DECLARATIONS, *GARDEN_DECLARATIONS)
    monkeypatch.setattr(problem_declarations, "HUB_PROBLEM_DECLARATIONS", union)
    monkeypatch.setattr(openapi_problems, "HUB_PROBLEM_DECLARATIONS", union)
    yield build_hub(tmp_path)


def _refused(
    hub: HubHarness,
    resp: Response,
    method: str,
    path: str,
    status: int,
    model: type[BaseModel],
    problem_type: str,
    detail: object,
) -> dict[str, Any]:
    assert hub.app is not None
    assert declared_refusal_mismatches(hub.app.openapi(), resp, method, path, status, model) == []
    body: dict[str, Any] = resp.json()
    assert (body["type"], body["status"], body["detail"]) == (problem_type, status, detail)
    return body


def _routine(hub: HubHarness, slug: str = "nightly") -> str:
    graph = hub.services.graph_mint.ensure_default(
        hub.services.default_graph_doc, definition_yaml=hub.services.default_graph_yaml
    )
    routine = hub.services.routine_authoring.create(
        project=project_record(hub, default_project(hub.engine)),
        ctx=_CTX,
        slug=slug,
        graph_name=graph.name,
        default_scope_slug=ScopeSlug.parse("blizzard"),
    )
    return routine.routine_id


def _seed_proposal(hub: HubHarness) -> None:
    with hub.engine.begin() as conn:
        conn.execute(s.scopes.insert().values(stamp(s.scopes, slug="blizzard", description="", created_at=_NOW)))
    FindingStore(hub_store_connections(hub.engine)).add(
        "fin_1",
        project_id=default_project(hub.engine),
        routine_name="nightly",
        scope_slug="blizzard",
        class_="stale-docstring",
        locus="a.py:1",
        summary="s1",
        introduced=None,
        at=_NOW,
    )
    GardenProposalStore(hub_store_connections(hub.engine)).create(
        "gprop_1",
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        findings=["fin_1"],
        at=_NOW,
    )


def test_an_unknown_routine_is_a_typed_404(hub: HubHarness) -> None:
    resp = hub.client.get("/api/routines/rt_missing")
    _refused(
        hub,
        resp,
        "get",
        "/api/routines/{routine_id}",
        404,
        types.RoutineNotFoundProblem,
        types.ROUTINE_NOT_FOUND,
        "unknown routine rt_missing",
    )


def test_a_retired_routine_refuses_a_run_as_not_runnable(hub: HubHarness) -> None:
    routine_id = _routine(hub)
    assert hub.client.post(f"/api/routines/{routine_id}/retire", json={}).status_code == 202
    resp = hub.client.post(f"/api/routines/{routine_id}/run", json={})
    assert resp.status_code == 503, resp.text
    _refused(
        hub,
        resp,
        "post",
        "/api/routines/{routine_id}/run",
        503,
        types.RoutineNotRunnableProblem,
        types.ROUTINE_NOT_RUNNABLE,
        resp.json()["detail"],
    )


def test_a_run_naming_an_unheld_scope_is_a_typed_422(hub: HubHarness) -> None:
    routine_id = _routine(hub)
    resp = hub.client.post(f"/api/routines/{routine_id}/run", json={"scope_slug": "elsewhere"})
    _refused(
        hub,
        resp,
        "post",
        "/api/routines/{routine_id}/run",
        422,
        types.ScopeNotFoundProblem,
        types.SCOPE_NOT_FOUND,
        "unknown scope 'elsewhere'",
    )


def test_a_routine_trend_over_an_unknown_routine_and_a_bad_instant(hub: HubHarness) -> None:
    window = {
        "since": "2026-01-01T00:00:00Z",
        "until": "2026-02-01T00:00:00Z",
        "introduced_boundary": "2026-01-15T00:00:00Z",
    }
    missing = hub.client.get("/api/routines/trend", params={"routine": "nope", **window})
    _refused(
        hub,
        missing,
        "get",
        "/api/routines/trend",
        404,
        types.RoutineNotFoundProblem,
        types.ROUTINE_NOT_FOUND,
        "unknown routine 'nope'",
    )
    _routine(hub)
    bad = hub.client.get(
        "/api/routines/trend", params={"routine": "nightly", "since": "x", "until": "y", "introduced_boundary": "z"}
    )
    _refused(
        hub,
        bad,
        "get",
        "/api/routines/trend",
        422,
        types.GardenWindowInvalidProblem,
        types.GARDEN_WINDOW_INVALID,
        "since 'x' is not a valid ISO-8601 instant",
    )


def test_an_unknown_scope_and_a_malformed_slug_are_typed(hub: HubHarness) -> None:
    missing = hub.client.get("/api/scopes/nope")
    _refused(
        hub,
        missing,
        "get",
        "/api/scopes/{slug}",
        404,
        types.ScopeNotFoundProblem,
        types.SCOPE_NOT_FOUND,
        "unknown scope nope",
    )
    with pytest.raises(ScopeSlugError) as caught:
        ScopeSlug.parse("Bad Slug!")
    malformed = hub.client.post("/api/scopes", json={"slug": "Bad Slug!"})
    _refused(
        hub,
        malformed,
        "post",
        "/api/scopes",
        422,
        types.ScopeSlugInvalidProblem,
        types.SCOPE_SLUG_INVALID,
        str(caught.value),
    )


def test_a_null_field_and_a_stale_revision_on_a_scope_are_typed(hub: HubHarness) -> None:
    assert hub.client.post("/api/scopes", json={"slug": "blizzard"}).status_code == 201
    null_field = hub.client.patch("/api/scopes/blizzard", json={"description": None})
    body = _refused(
        hub,
        null_field,
        "patch",
        "/api/scopes/{slug}",
        422,
        admin_types.ConfigFieldInvalidProblem,
        admin_types.CONFIG_FIELD_INVALID,
        null_field.json()["detail"],
    )
    assert [(entry["loc"], entry["type"]) for entry in body["detail"]] == [(["body", "description"], "value_error")]
    stale = hub.client.patch("/api/scopes/blizzard", json={"description": "x"}, headers={"If-Match": "99"})
    _refused(
        hub,
        stale,
        "patch",
        "/api/scopes/{slug}",
        409,
        RevisionConflictProblem,
        REVISION_CONFLICT,
        stale.json()["detail"],
    )


def test_an_unknown_cadence_is_a_typed_404(hub: HubHarness) -> None:
    resp = hub.client.get("/api/cadences/cd_missing")
    _refused(
        hub,
        resp,
        "get",
        "/api/cadences/{cadence_id}",
        404,
        types.CadenceNotFoundProblem,
        types.CADENCE_NOT_FOUND,
        "unknown cadence cd_missing",
    )


def test_an_unknown_garden_proposal_is_a_typed_404(hub: HubHarness) -> None:
    resp = hub.client.get("/api/garden-proposals/gprop_missing")
    _refused(
        hub,
        resp,
        "get",
        "/api/garden-proposals/{proposal_id}",
        404,
        types.GardenProposalNotFoundProblem,
        types.GARDEN_PROPOSAL_NOT_FOUND,
        "unknown garden proposal gprop_missing",
    )


def test_a_blank_pass_reason_and_a_closed_proposal_are_typed(hub: HubHarness) -> None:
    _seed_proposal(hub)
    blank = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "   "})
    _refused(
        hub,
        blank,
        "post",
        "/api/garden-proposals/{proposal_id}/pass",
        422,
        types.GardenProposalInvalidProblem,
        types.GARDEN_PROPOSAL_INVALID,
        blank.json()["detail"],
    )
    assert hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "no"}).status_code == 200
    closed = hub.client.post("/api/garden-proposals/gprop_1/pass", json={"reason": "again"})
    _refused(
        hub,
        closed,
        "post",
        "/api/garden-proposals/{proposal_id}/pass",
        409,
        types.GardenProposalAlreadyClosedProblem,
        types.GARDEN_PROPOSAL_ALREADY_CLOSED,
        closed.json()["detail"],
    )


def test_an_unknown_finding_id_on_attach_is_a_typed_422(hub: HubHarness) -> None:
    _seed_proposal(hub)
    resp = hub.client.post("/api/garden-proposals/gprop_1/attach", json={"findings": ["fin_nope"]})
    _refused(
        hub,
        resp,
        "post",
        "/api/garden-proposals/{proposal_id}/attach",
        422,
        types.GardenProposalInvalidProblem,
        types.GARDEN_PROPOSAL_INVALID,
        "unknown finding id(s): fin_nope",
    )


def test_a_proposal_naming_an_unknown_routine_is_a_typed_422(hub: HubHarness) -> None:
    resp = hub.client.post(
        "/api/garden-proposals",
        json={"routine": "ghost", "class": "c", "title": "t", "body": "b", "findings": []},
    )
    _refused(
        hub,
        resp,
        "post",
        "/api/garden-proposals",
        422,
        types.RoutineNotFoundProblem,
        types.ROUTINE_NOT_FOUND,
        "unknown routine 'ghost'",
    )


def test_runs_refuse_a_bad_instant_and_an_unknown_run(hub: HubHarness) -> None:
    bad = hub.client.get("/api/runs", params={"since": "soon"})
    _refused(
        hub,
        bad,
        "get",
        "/api/runs",
        422,
        types.GardenWindowInvalidProblem,
        types.GARDEN_WINDOW_INVALID,
        "since 'soon' is not a valid ISO-8601 instant",
    )
    missing = hub.client.get("/api/runs/ch_missing")
    _refused(
        hub,
        missing,
        "get",
        "/api/runs/{chunk_id}",
        404,
        types.RunNotFoundProblem,
        types.RUN_NOT_FOUND,
        "unknown run ch_missing",
    )


def test_an_unknown_sweep_is_a_typed_404_on_both_doors(hub: HubHarness) -> None:
    tenant = hub.client.post("/api/sweeps/nope/passes")
    body = _refused(
        hub,
        tenant,
        "post",
        "/api/sweeps/{name}/passes",
        404,
        types.SweepNotFoundProblem,
        types.SWEEP_NOT_FOUND,
        tenant.json()["detail"],
    )
    assert "nope" in body["detail"]


def test_a_tenant_pass_of_an_every_tenant_sweep_is_a_typed_422(hub: HubHarness) -> None:
    resp = hub.client.post("/api/sweeps/tenant_teardown/passes")
    _refused(
        hub,
        resp,
        "post",
        "/api/sweeps/{name}/passes",
        422,
        types.SweepServesNoTenantProblem,
        types.SWEEP_SERVES_NO_TENANT,
        resp.json()["detail"],
    )
