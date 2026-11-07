"""Factories for the garden family's problems; each takes the refusal's own words and keeps them as ``detail``."""

from __future__ import annotations

from blizzard.wire import problem_types_admin as admin_types
from blizzard.wire import problem_types_garden as types
from blizzard.wire.problem import ValidationEntry
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


def routine_not_found_problem(detail: str, status: int, *, instance: str) -> RoutineNotFoundProblem:
    return RoutineNotFoundProblem(
        type=types.ROUTINE_NOT_FOUND, title="Routine not found", detail=detail, instance=instance, status=status
    )


def scope_not_found_problem(detail: str, status: int, *, instance: str) -> ScopeNotFoundProblem:
    return ScopeNotFoundProblem(
        type=types.SCOPE_NOT_FOUND, title="Scope not found", detail=detail, instance=instance, status=status
    )


def cadence_not_found_problem(detail: str, *, instance: str) -> CadenceNotFoundProblem:
    return CadenceNotFoundProblem(
        type=types.CADENCE_NOT_FOUND, title="Cadence not found", detail=detail, instance=instance, status=404
    )


def garden_proposal_not_found_problem(detail: str, *, instance: str) -> GardenProposalNotFoundProblem:
    return GardenProposalNotFoundProblem(
        type=types.GARDEN_PROPOSAL_NOT_FOUND,
        title="Garden proposal not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def run_not_found_problem(detail: str, *, instance: str) -> RunNotFoundProblem:
    return RunNotFoundProblem(
        type=types.RUN_NOT_FOUND, title="Run not found", detail=detail, instance=instance, status=404
    )


def sweep_not_found_problem(detail: str, *, instance: str) -> SweepNotFoundProblem:
    return SweepNotFoundProblem(
        type=types.SWEEP_NOT_FOUND, title="Sweep not found", detail=detail, instance=instance, status=404
    )


def sweep_serves_no_tenant_problem(detail: str, *, instance: str) -> SweepServesNoTenantProblem:
    return SweepServesNoTenantProblem(
        type=types.SWEEP_SERVES_NO_TENANT,
        title="Sweep serves no tenant",
        detail=detail,
        instance=instance,
        status=422,
    )


def cadence_admission_refused_problem(detail: str, *, instance: str) -> CadenceAdmissionRefusedProblem:
    return CadenceAdmissionRefusedProblem(
        type=types.CADENCE_ADMISSION_REFUSED,
        title="Cadence admission refused",
        detail=detail,
        instance=instance,
        status=403,
    )


def cadence_slug_taken_problem(detail: str, *, instance: str) -> CadenceSlugTakenProblem:
    return CadenceSlugTakenProblem(
        type=types.CADENCE_SLUG_TAKEN, title="Cadence slug taken", detail=detail, instance=instance, status=422
    )


def config_field_invalid_problem(field: str, message: str, *, instance: str) -> admin_types.ConfigFieldInvalidProblem:
    """The refused field, located in the body — the shape FastAPI's own validation answers."""
    return admin_types.ConfigFieldInvalidProblem(
        type=admin_types.CONFIG_FIELD_INVALID,
        title="Field invalid",
        detail=[ValidationEntry(loc=["body", field], msg=message, type="value_error")],
        instance=instance,
        status=422,
    )


def scope_slug_invalid_problem(detail: str, *, instance: str) -> ScopeSlugInvalidProblem:
    return ScopeSlugInvalidProblem(
        type=types.SCOPE_SLUG_INVALID, title="Scope slug invalid", detail=detail, instance=instance, status=422
    )


def routine_definition_invalid_problem(detail: str, *, instance: str) -> RoutineDefinitionInvalidProblem:
    return RoutineDefinitionInvalidProblem(
        type=types.ROUTINE_DEFINITION_INVALID,
        title="Routine definition invalid",
        detail=detail,
        instance=instance,
        status=422,
    )


def routine_scope_not_related_problem(detail: str, *, instance: str) -> RoutineScopeNotRelatedProblem:
    return RoutineScopeNotRelatedProblem(
        type=types.ROUTINE_SCOPE_NOT_RELATED,
        title="Scope not related to routine",
        detail=detail,
        instance=instance,
        status=422,
    )


def routine_not_runnable_problem(detail: str, *, instance: str) -> RoutineNotRunnableProblem:
    return RoutineNotRunnableProblem(
        type=types.ROUTINE_NOT_RUNNABLE, title="Routine not runnable", detail=detail, instance=instance, status=503
    )


def garden_window_invalid_problem(detail: str, *, instance: str) -> GardenWindowInvalidProblem:
    return GardenWindowInvalidProblem(
        type=types.GARDEN_WINDOW_INVALID, title="Window invalid", detail=detail, instance=instance, status=422
    )


def garden_proposal_already_closed_problem(detail: str, *, instance: str) -> GardenProposalAlreadyClosedProblem:
    return GardenProposalAlreadyClosedProblem(
        type=types.GARDEN_PROPOSAL_ALREADY_CLOSED,
        title="Garden proposal already closed",
        detail=detail,
        instance=instance,
        status=409,
    )


def garden_proposal_invalid_problem(detail: str, *, instance: str) -> GardenProposalInvalidProblem:
    return GardenProposalInvalidProblem(
        type=types.GARDEN_PROPOSAL_INVALID,
        title="Garden proposal invalid",
        detail=detail,
        instance=instance,
        status=422,
    )


def garden_default_graph_retired_problem(detail: str, *, instance: str) -> GardenDefaultGraphRetiredProblem:
    return GardenDefaultGraphRetiredProblem(
        type=types.GARDEN_DEFAULT_GRAPH_RETIRED,
        title="Default graph retired",
        detail=detail,
        instance=instance,
        status=503,
    )
