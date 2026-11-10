"""Factories for the garden family's problems; each takes the refusal's own words and keeps them as ``detail``."""

from __future__ import annotations

from blizzard.wire import cadence as cadence_wire
from blizzard.wire import config as config_wire
from blizzard.wire import garden_proposal as garden_proposal_wire
from blizzard.wire import garden_run as garden_run_wire
from blizzard.wire import garden_sweeps as garden_sweeps_wire
from blizzard.wire import garden_window as garden_window_wire
from blizzard.wire import routine as routine_wire
from blizzard.wire import scope as scope_wire
from blizzard.wire.cadence import CadenceAdmissionRefusedProblem, CadenceNotFoundProblem, CadenceSlugTakenProblem
from blizzard.wire.garden_proposal import (
    GardenProposalAlreadyClosedProblem,
    GardenProposalInvalidProblem,
    GardenProposalNotFoundProblem,
)
from blizzard.wire.garden_run import RunNotFoundProblem
from blizzard.wire.garden_sweeps import SweepNotFoundProblem, SweepServesNoTenantProblem
from blizzard.wire.garden_window import GardenWindowInvalidProblem
from blizzard.wire.problem import ValidationEntry
from blizzard.wire.routine import (
    RoutineDefinitionInvalidProblem,
    RoutineNotFoundProblem,
    RoutineNotRunnableProblem,
    RoutineScopeNotRelatedProblem,
)
from blizzard.wire.scope import ScopeNotFoundProblem, ScopeSlugInvalidProblem


def routine_not_found_problem(detail: str, status: int, *, instance: str) -> RoutineNotFoundProblem:
    return RoutineNotFoundProblem(
        type=routine_wire.ROUTINE_NOT_FOUND, title="Routine not found", detail=detail, instance=instance, status=status
    )


def scope_not_found_problem(detail: str, status: int, *, instance: str) -> ScopeNotFoundProblem:
    return ScopeNotFoundProblem(
        type=scope_wire.SCOPE_NOT_FOUND, title="Scope not found", detail=detail, instance=instance, status=status
    )


def cadence_not_found_problem(detail: str, *, instance: str) -> CadenceNotFoundProblem:
    return CadenceNotFoundProblem(
        type=cadence_wire.CADENCE_NOT_FOUND, title="Cadence not found", detail=detail, instance=instance, status=404
    )


def garden_proposal_not_found_problem(detail: str, *, instance: str) -> GardenProposalNotFoundProblem:
    return GardenProposalNotFoundProblem(
        type=garden_proposal_wire.GARDEN_PROPOSAL_NOT_FOUND,
        title="Garden proposal not found",
        detail=detail,
        instance=instance,
        status=404,
    )


def run_not_found_problem(detail: str, *, instance: str) -> RunNotFoundProblem:
    return RunNotFoundProblem(
        type=garden_run_wire.RUN_NOT_FOUND, title="Run not found", detail=detail, instance=instance, status=404
    )


def sweep_not_found_problem(detail: str, *, instance: str) -> SweepNotFoundProblem:
    return SweepNotFoundProblem(
        type=garden_sweeps_wire.SWEEP_NOT_FOUND, title="Sweep not found", detail=detail, instance=instance, status=404
    )


def sweep_serves_no_tenant_problem(detail: str, *, instance: str) -> SweepServesNoTenantProblem:
    return SweepServesNoTenantProblem(
        type=garden_sweeps_wire.SWEEP_SERVES_NO_TENANT,
        title="Sweep serves no tenant",
        detail=detail,
        instance=instance,
        status=422,
    )


def cadence_admission_refused_problem(detail: str, *, instance: str) -> CadenceAdmissionRefusedProblem:
    return CadenceAdmissionRefusedProblem(
        type=cadence_wire.CADENCE_ADMISSION_REFUSED,
        title="Cadence admission refused",
        detail=detail,
        instance=instance,
        status=403,
    )


def cadence_slug_taken_problem(detail: str, *, instance: str) -> CadenceSlugTakenProblem:
    return CadenceSlugTakenProblem(
        type=cadence_wire.CADENCE_SLUG_TAKEN, title="Cadence slug taken", detail=detail, instance=instance, status=422
    )


def config_field_invalid_problem(field: str, message: str, *, instance: str) -> config_wire.ConfigFieldInvalidProblem:
    """The refused field, located in the body — the shape FastAPI's own validation answers."""
    return config_wire.ConfigFieldInvalidProblem(
        type=config_wire.CONFIG_FIELD_INVALID,
        title="Field invalid",
        detail=[ValidationEntry(loc=["body", field], msg=message, type="value_error")],
        instance=instance,
        status=422,
    )


def scope_slug_invalid_problem(detail: str, *, instance: str) -> ScopeSlugInvalidProblem:
    return ScopeSlugInvalidProblem(
        type=scope_wire.SCOPE_SLUG_INVALID, title="Scope slug invalid", detail=detail, instance=instance, status=422
    )


def routine_definition_invalid_problem(detail: str, *, instance: str) -> RoutineDefinitionInvalidProblem:
    return RoutineDefinitionInvalidProblem(
        type=routine_wire.ROUTINE_DEFINITION_INVALID,
        title="Routine definition invalid",
        detail=detail,
        instance=instance,
        status=422,
    )


def routine_scope_not_related_problem(detail: str, *, instance: str) -> RoutineScopeNotRelatedProblem:
    return RoutineScopeNotRelatedProblem(
        type=routine_wire.ROUTINE_SCOPE_NOT_RELATED,
        title="Scope not related to routine",
        detail=detail,
        instance=instance,
        status=422,
    )


def routine_not_runnable_problem(detail: str, *, instance: str) -> RoutineNotRunnableProblem:
    return RoutineNotRunnableProblem(
        type=routine_wire.ROUTINE_NOT_RUNNABLE,
        title="Routine not runnable",
        detail=detail,
        instance=instance,
        status=503,
    )


def garden_window_invalid_problem(detail: str, *, instance: str) -> GardenWindowInvalidProblem:
    return GardenWindowInvalidProblem(
        type=garden_window_wire.GARDEN_WINDOW_INVALID,
        title="Window invalid",
        detail=detail,
        instance=instance,
        status=422,
    )


def garden_proposal_already_closed_problem(detail: str, *, instance: str) -> GardenProposalAlreadyClosedProblem:
    return GardenProposalAlreadyClosedProblem(
        type=garden_proposal_wire.GARDEN_PROPOSAL_ALREADY_CLOSED,
        title="Garden proposal already closed",
        detail=detail,
        instance=instance,
        status=409,
    )


def garden_proposal_invalid_problem(detail: str, *, instance: str) -> GardenProposalInvalidProblem:
    return GardenProposalInvalidProblem(
        type=garden_proposal_wire.GARDEN_PROPOSAL_INVALID,
        title="Garden proposal invalid",
        detail=detail,
        instance=instance,
        status=422,
    )
