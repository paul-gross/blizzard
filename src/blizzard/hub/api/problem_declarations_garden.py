"""The problem variants the garden family's operations declare: routines, cadences, scopes, garden proposals,
runs, and sweeps."""

from __future__ import annotations

from blizzard.hub.api.problem_declaration import ProblemDeclaration
from blizzard.wire import problem_types_admin as admin_t
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.problem_types_garden import (
    CadenceAdmissionRefusedProblem,
    CadenceNotFoundProblem,
    CadenceSlugTakenProblem,
    GardenDefaultGraphRetiredProblem,
    GardenProposalAlreadyClosedProblem,
    GardenProposalInvalidProblem,
    GardenProposalNotFoundProblem,
    GardenWindowInvalidProblem,
    RoutineDefinitionInvalidProblem,
    RoutineNotFoundProblem,
    RoutineNotRunnableProblem,
    RoutineScopeNotRelatedProblem,
    RunNotFoundProblem,
    ScopeNotFoundProblem,
    ScopeSlugInvalidProblem,
    SweepNotFoundProblem,
    SweepServesNoTenantProblem,
)

_PROJECT = "/api/projects/{project}"
_ROUTINE_CREATES = ("/api/routines", f"{_PROJECT}/routines")
_SCOPE_ROOTS = ("/api/scopes", f"{_PROJECT}/scopes")
_PROPOSAL_CREATES = ("/api/garden-proposals", f"{_PROJECT}/garden-proposals")
_RUN_ROOTS = ("/api/runs", f"{_PROJECT}/runs")

_ROUTINE_WRITE = "/api/routines/{routine_id}"

ROUTINE_DECLARATIONS = (
    *(
        ProblemDeclaration("POST", path, 422, model)
        for path in _ROUTINE_CREATES
        for model in (ScopeSlugInvalidProblem, RoutineDefinitionInvalidProblem, admin_t.ConfigFieldInvalidProblem)
    ),
    ProblemDeclaration("GET", "/api/routines/trend", 404, RoutineNotFoundProblem),
    ProblemDeclaration("GET", "/api/routines/trend", 422, GardenWindowInvalidProblem),
    ProblemDeclaration("GET", "/api/routines/proposal-counts", 404, RoutineNotFoundProblem),
    ProblemDeclaration("GET", "/api/routines/proposal-counts", 422, GardenWindowInvalidProblem),
    *(
        ProblemDeclaration("GET", f"{_ROUTINE_WRITE}{suffix}", 404, RoutineNotFoundProblem)
        for suffix in ("", "/baselines", "/scopes", "/sweeps")
    ),
    ProblemDeclaration("GET", f"{_ROUTINE_WRITE}/sweeps", 422, GardenWindowInvalidProblem),
    *(
        ProblemDeclaration(method, f"{_ROUTINE_WRITE}/scopes/{{scope_slug}}", status, model)
        for method in ("PUT", "DELETE")
        for status, model in (
            (404, RoutineNotFoundProblem),
            (404, ScopeNotFoundProblem),
            (409, RevisionConflictProblem),
            (422, ScopeSlugInvalidProblem),
            (422, RoutineDefinitionInvalidProblem),
        )
    ),
    *(
        ProblemDeclaration("PATCH", _ROUTINE_WRITE, status, model)
        for status, model in (
            (404, RoutineNotFoundProblem),
            (409, RevisionConflictProblem),
            (422, admin_t.ConfigFieldInvalidProblem),
            (422, ScopeSlugInvalidProblem),
            (422, RoutineDefinitionInvalidProblem),
        )
    ),
    *(
        ProblemDeclaration("POST", f"{_ROUTINE_WRITE}/{verb}", status, model)
        for verb in ("retire", "enable")
        for status, model in ((404, RoutineNotFoundProblem), (409, RevisionConflictProblem))
    ),
    *(
        ProblemDeclaration("POST", f"{_ROUTINE_WRITE}/run", status, model)
        for status, model in (
            (404, RoutineNotFoundProblem),
            (422, ScopeSlugInvalidProblem),
            (422, ScopeNotFoundProblem),
            (422, RoutineScopeNotRelatedProblem),
            (503, RoutineNotRunnableProblem),
        )
    ),
)

CADENCE_DECLARATIONS = (
    *(
        ProblemDeclaration("POST", f"{_PROJECT}/cadences", status, model)
        for status, model in (
            (403, CadenceAdmissionRefusedProblem),
            (422, admin_t.ConfigFieldInvalidProblem),
            (422, CadenceSlugTakenProblem),
        )
    ),
    *(
        ProblemDeclaration("GET", f"/api/cadences/{{cadence_id}}{suffix}", 404, CadenceNotFoundProblem)
        for suffix in ("", "/errands")
    ),
    *(
        ProblemDeclaration("PATCH", "/api/cadences/{cadence_id}", status, model)
        for status, model in (
            (403, CadenceAdmissionRefusedProblem),
            (404, CadenceNotFoundProblem),
            (422, admin_t.ConfigFieldInvalidProblem),
        )
    ),
    ProblemDeclaration("POST", "/api/cadences/{cadence_id}/retire", 404, CadenceNotFoundProblem),
    ProblemDeclaration("POST", "/api/cadences/{cadence_id}/enable", 403, CadenceAdmissionRefusedProblem),
    ProblemDeclaration("POST", "/api/cadences/{cadence_id}/enable", 404, CadenceNotFoundProblem),
)

SCOPE_DECLARATIONS = (
    *(ProblemDeclaration("POST", root, 422, ScopeSlugInvalidProblem) for root in _SCOPE_ROOTS),
    *(
        ProblemDeclaration("GET", f"{root}/{{slug}}{suffix}", 404, ScopeNotFoundProblem)
        for root in _SCOPE_ROOTS
        for suffix in ("", "/routines")
    ),
    *(
        ProblemDeclaration("PATCH", f"{root}/{{slug}}", status, model)
        for root in _SCOPE_ROOTS
        for status, model in (
            (404, ScopeNotFoundProblem),
            (409, RevisionConflictProblem),
            (422, ScopeSlugInvalidProblem),
            (422, admin_t.ConfigFieldInvalidProblem),
        )
    ),
    *(
        ProblemDeclaration("POST", f"{root}/{{slug}}/{verb}", status, model)
        for root in _SCOPE_ROOTS
        for verb in ("retire", "enable")
        for status, model in ((404, ScopeNotFoundProblem), (409, RevisionConflictProblem))
    ),
)

_PROPOSAL = "/api/garden-proposals/{proposal_id}"

GARDEN_PROPOSAL_DECLARATIONS = (
    ProblemDeclaration("GET", _PROPOSAL, 404, GardenProposalNotFoundProblem),
    *(
        ProblemDeclaration(method, path, status, model)
        for method, path in (
            ("POST", f"{_PROPOSAL}/pass"),
            ("POST", f"{_PROPOSAL}/accept"),
            ("PATCH", _PROPOSAL),
            ("POST", f"{_PROPOSAL}/attach"),
            ("POST", f"{_PROPOSAL}/detach"),
        )
        for status, model in (
            (404, GardenProposalNotFoundProblem),
            (409, GardenProposalAlreadyClosedProblem),
            (422, GardenProposalInvalidProblem),
        )
    ),
    ProblemDeclaration("POST", f"{_PROPOSAL}/accept", 503, GardenDefaultGraphRetiredProblem),
    *(
        ProblemDeclaration("POST", path, 422, model)
        for path in _PROPOSAL_CREATES
        for model in (GardenProposalInvalidProblem, RoutineNotFoundProblem)
    ),
)

RUN_DECLARATIONS = (
    *(ProblemDeclaration("GET", path, 422, GardenWindowInvalidProblem) for path in _RUN_ROOTS),
    *(ProblemDeclaration("GET", f"{path}/{{chunk_id}}", 404, RunNotFoundProblem) for path in _RUN_ROOTS),
)

SWEEP_DECLARATIONS = (
    ProblemDeclaration("POST", "/api/sweeps/{name}/passes", 404, SweepNotFoundProblem),
    ProblemDeclaration("POST", "/api/sweeps/{name}/passes", 422, SweepServesNoTenantProblem),
    ProblemDeclaration("POST", "/api/admin/sweeps/{name}/passes", 404, SweepNotFoundProblem),
)

GARDEN_DECLARATIONS: tuple[ProblemDeclaration, ...] = (
    *ROUTINE_DECLARATIONS,
    *CADENCE_DECLARATIONS,
    *SCOPE_DECLARATIONS,
    *GARDEN_PROPOSAL_DECLARATIONS,
    *RUN_DECLARATIONS,
    *SWEEP_DECLARATIONS,
)
