"""The cadence sweep on a production-wired hub (component tier).

A pass judges every errand of every live cadence and mints each due one in its own tenant — up to the pass bound,
the rest on the next pass. A hand-started run of the same routine and scope neither counts as the errand's run nor
holds it, and while the ``cadences`` flag is off a pass mints nothing."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.flags import resolve_flags
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.cadence.errands import Errand, ErrandRun
from blizzard.hub.domain.cadence.mint import ErrandInputs, MintedErrand
from blizzard.hub.domain.cadence.model import CadenceShape, ChoreShape
from blizzard.hub.domain.cadence.sweep import CADENCE_PASS_LIMIT
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import WorkItemAuthor, WorkItemAuthorKind, WorkRef
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.runs.run import RoutineRetiredError
from blizzard.hub.domain.garden.scopes import ScopeSlug
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.tenancy import TenantId
from tests.support.config_inputs import FLAG_REGISTRY
from tests.support.scaffolding import HubHarness, build_hub, default_project, default_tenant, hub_scoped

_CTX = ChangeContext(actor="operator", door=Door.API)
_CADENCES_ON = resolve_flags(FLAG_REGISTRY, "hub", {"cadences": True}).flags


def _hub(tmp_path: Path, *, on: bool = True) -> HubHarness:
    return build_hub(tmp_path, flags=_CADENCES_ON if on else None)


def _project(hub: HubHarness, services: TenantServices, tenant: TenantId) -> Project:
    project = services.projects.get(ProjectId(default_project(hub.engine, tenant)))
    assert project is not None
    return project


def _routine(services: TenantServices, project: Project, slug: str, *linked: str) -> Routine:
    graph = services.graph_mint.ensure_default(services.default_graph_doc, definition_yaml=services.default_graph_yaml)
    routine = services.routine_authoring.create(
        project=project, ctx=_CTX, slug=slug, graph_name=graph.name, default_scope_slug=ScopeSlug.parse(slug)
    )
    for scope_slug in linked:
        scope = services.scope_registry.ensure(project, ScopeSlug.parse(scope_slug), _CTX)
        routine = services.routine_scope_membership.link(routine, scope, _CTX)
    return routine


def _cadence(services: TenantServices, project: Project, *chores: ChoreShape) -> None:
    services.cadence_authoring.create(
        CadenceShape(slug="nightly", spacing="1h", chores=chores), project=project, ctx=_CTX, may_admit=True
    )


def _sweep(hub: HubHarness) -> None:
    assert hub.hub_services.sweeps.pass_every_tenant("cadence").error is None


def _runs(hub: HubHarness, tenant: TenantId) -> list[tuple[str, str]]:
    """``tenant``'s routine runs as (author kind, scope slug)."""
    query = (
        sa.select(s.work_items.c.author_kind, s.scopes.c.slug.label("scope_slug"))
        .join(s.work_item_runs, s.work_item_runs.c.work_item_id == s.work_items.c.work_item_id)
        .join(s.scopes, s.scopes.c.scope_id == s.work_item_runs.c.scope_id)
        .where(s.work_items.c.tenant_id == tenant)
    )
    with hub.engine.connect() as conn:
        return [(row.author_kind, row.scope_slug) for row in conn.execute(hub_scoped(query))]


def _statuses(services: TenantServices) -> list[ChunkStatus]:
    return sorted(services.chunks.facts.load_live_statuses().values())


def test_a_pass_mints_each_due_errand_promoted_or_held_and_then_waits_on_its_live_run(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    project = _project(hub, hub.services, default_tenant(hub.engine))
    _routine(hub.services, project, "sweep", "runner")
    _cadence(
        hub.services,
        project,
        ChoreShape("every", "sweep", scopes="all"),
        ChoreShape("held", "sweep", scopes=["runner"], promote=False),
    )

    _sweep(hub)
    _sweep(hub)

    tenant = default_tenant(hub.engine)
    assert sorted(_runs(hub, tenant)) == [("cadence", "runner"), ("cadence", "runner"), ("cadence", "sweep")]
    assert _statuses(hub.services) == sorted([ChunkStatus.READY, ChunkStatus.READY, ChunkStatus.NOT_READY])


def test_a_pass_with_the_flag_off_mints_nothing(tmp_path: Path) -> None:
    hub = _hub(tmp_path, on=False)
    project = _project(hub, hub.services, default_tenant(hub.engine))
    _routine(hub.services, project, "sweep")
    _cadence(hub.services, project, ChoreShape("plain", "sweep"))

    _sweep(hub)
    assert hub.hub_services.sweeps.pass_one_tenant("cadence", default_tenant(hub.engine)).error is None

    assert _runs(hub, default_tenant(hub.engine)) == []


def test_one_pass_mints_each_tenants_errands_in_that_tenant(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    carried = default_tenant(hub.engine)
    other = hub.add_tenant("other")
    for services, tenant, slug in ((hub.services, carried, "alpha"), (other.services, other.tenant, "beta")):
        project = _project(hub, services, tenant)
        _routine(services, project, slug)
        _cadence(services, project, ChoreShape("plain", slug))

    _sweep(hub)

    assert _runs(hub, carried) == [("cadence", "alpha")]
    assert _runs(hub, other.tenant) == [("cadence", "beta")]


def test_a_pass_mints_up_to_its_bound_and_the_next_pass_mints_the_rest(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    tenant = default_tenant(hub.engine)
    project = _project(hub, hub.services, tenant)
    linked = [f"scope-{n:02}" for n in range(CADENCE_PASS_LIMIT + 1)]
    _routine(hub.services, project, "sweep", *linked)
    _cadence(hub.services, project, ChoreShape("every", "sweep", scopes="all"))

    _sweep(hub)
    assert len(_runs(hub, tenant)) == CADENCE_PASS_LIMIT

    _sweep(hub)
    assert sorted(scope for _, scope in _runs(hub, tenant)) == sorted(["sweep", *linked])


def test_a_hand_started_run_neither_counts_as_the_errands_run_nor_holds_it(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    tenant = default_tenant(hub.engine)
    project = _project(hub, hub.services, tenant)
    routine = _routine(hub.services, project, "sweep")
    scope = hub.services.scopes.get(project.project_id, "sweep")
    assert scope is not None
    hub.services.routine_run.run(
        routine, scope=scope, mode=RunMode.FULL, note=None, author=WorkItemAuthor.user("operator")
    )
    _cadence(hub.services, project, ChoreShape("plain", "sweep"))

    _sweep(hub)

    assert sorted(_runs(hub, tenant)) == [("cadence", "sweep"), (WorkItemAuthorKind.USER.value, "sweep")]


def test_a_refused_errand_counts_as_refused_and_the_pass_mints_the_others(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _hub(tmp_path)
    tenant = default_tenant(hub.engine)
    project = _project(hub, hub.services, tenant)
    _routine(hub.services, project, "sweep", "retired", "taken")
    _cadence(hub.services, project, ChoreShape("every", "sweep", scopes="all"))
    mint = hub.services.cadence_mint.mint
    refusals = {
        "retired": RoutineRetiredError("sweep"),
        "taken": IngestConflict(existing_chunk_id="chk_held", pointer=WorkRef(source="hub", ref="1")),
    }

    def refusing(errand: Errand, *, judged: ErrandRun | None, inputs: ErrandInputs) -> MintedErrand | None:
        refusal = refusals.get(errand.scope_slug)
        if refusal is not None:
            raise refusal
        return mint(errand, judged=judged, inputs=inputs)

    monkeypatch.setattr(hub.services.cadence_mint, "mint", refusing)

    with capture_logs() as logs:
        _sweep(hub)

    assert _runs(hub, tenant) == [("cadence", "sweep")]
    refused = sorted(log["scope"] for log in logs if log["event"] == "cadence errand refused")
    assert refused == ["retired", "taken"]
    tallied = next(log for log in logs if log["event"] == "cadence tenant pass tallied")
    assert (tallied["due"], tallied["minted"], tallied["refused"], tallied["failed"]) == (3, 1, 2, 0)


def test_a_failed_announcement_is_logged_and_the_committed_mint_still_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = _hub(tmp_path)
    tenant = default_tenant(hub.engine)
    project = _project(hub, hub.services, tenant)
    _routine(hub.services, project, "sweep", "runner")
    _cadence(hub.services, project, ChoreShape("every", "sweep", scopes="all"))

    def refuse() -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(hub.services.events, "publish_queue_changed", refuse)

    with capture_logs() as logs:
        _sweep(hub)

    assert sorted(_runs(hub, tenant)) == [("cadence", "runner"), ("cadence", "sweep")]
    assert [log["log_level"] for log in logs if log["event"] == "cadence errand mint announcement failed"] == [
        "warning",
        "warning",
    ]
    completed = next(log for log in logs if log["event"] == "cadence sweep completed")
    assert (completed["minted"], completed["failed"], completed["tenants_failed"]) == (2, 0, [])
