"""The garden family's problem types and wire models: routines, cadences, scopes, garden proposals, runs, sweeps.

Each type names one meaning. Its ``detail`` is the refusal's own words (or, for a refused field, the
validation-shaped list), unchanged from before the refusal became a typed problem.
"""

from typing import Final, Literal

from blizzard.wire.problem import ProblemDetails

ROUTINE_NOT_FOUND: Final = "urn:blizzard:problem:routine-not-found"
SCOPE_NOT_FOUND: Final = "urn:blizzard:problem:scope-not-found"
CADENCE_NOT_FOUND: Final = "urn:blizzard:problem:cadence-not-found"
GARDEN_PROPOSAL_NOT_FOUND: Final = "urn:blizzard:problem:garden-proposal-not-found"
RUN_NOT_FOUND: Final = "urn:blizzard:problem:run-not-found"
SWEEP_NOT_FOUND: Final = "urn:blizzard:problem:sweep-not-found"
SWEEP_SERVES_NO_TENANT: Final = "urn:blizzard:problem:sweep-serves-no-tenant"
CADENCE_ADMISSION_REFUSED: Final = "urn:blizzard:problem:cadence-admission-refused"
CADENCE_SLUG_TAKEN: Final = "urn:blizzard:problem:cadence-slug-taken"
SCOPE_SLUG_INVALID: Final = "urn:blizzard:problem:scope-slug-invalid"
ROUTINE_DEFINITION_INVALID: Final = "urn:blizzard:problem:routine-definition-invalid"
ROUTINE_SCOPE_NOT_RELATED: Final = "urn:blizzard:problem:routine-scope-not-related"
ROUTINE_NOT_RUNNABLE: Final = "urn:blizzard:problem:routine-not-runnable"
GARDEN_WINDOW_INVALID: Final = "urn:blizzard:problem:garden-window-invalid"
GARDEN_PROPOSAL_ALREADY_CLOSED: Final = "urn:blizzard:problem:garden-proposal-already-closed"
GARDEN_PROPOSAL_INVALID: Final = "urn:blizzard:problem:garden-proposal-invalid"
GARDEN_DEFAULT_GRAPH_RETIRED: Final = "urn:blizzard:problem:garden-default-graph-retired"

GARDEN_PROBLEM_TYPES: frozenset[str] = frozenset(
    {
        ROUTINE_NOT_FOUND,
        SCOPE_NOT_FOUND,
        CADENCE_NOT_FOUND,
        GARDEN_PROPOSAL_NOT_FOUND,
        RUN_NOT_FOUND,
        SWEEP_NOT_FOUND,
        SWEEP_SERVES_NO_TENANT,
        CADENCE_ADMISSION_REFUSED,
        CADENCE_SLUG_TAKEN,
        SCOPE_SLUG_INVALID,
        ROUTINE_DEFINITION_INVALID,
        ROUTINE_SCOPE_NOT_RELATED,
        ROUTINE_NOT_RUNNABLE,
        GARDEN_WINDOW_INVALID,
        GARDEN_PROPOSAL_ALREADY_CLOSED,
        GARDEN_PROPOSAL_INVALID,
        GARDEN_DEFAULT_GRAPH_RETIRED,
    }
)


class RoutineNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-not-found"]]):
    """The named routine does not exist in the project (404 on a read or write, 422 when a body names it)."""


class ScopeNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:scope-not-found"]]):
    """The named scope does not exist in the project (404 on a read or write, 422 when a run names it)."""


class CadenceNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:cadence-not-found"]]):
    """The named cadence does not exist in the tenant."""


class GardenProposalNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:garden-proposal-not-found"]]):
    """The named garden proposal does not exist in the tenant."""


class RunNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:run-not-found"]]):
    """The named garden run does not exist (in the project, when the request is project-scoped)."""


class SweepNotFoundProblem(ProblemDetails[Literal["urn:blizzard:problem:sweep-not-found"]]):
    """No sweep this hub builds goes by the name."""


class SweepServesNoTenantProblem(ProblemDetails[Literal["urn:blizzard:problem:sweep-serves-no-tenant"]]):
    """The sweep runs over every tenant only, so a single tenant's pass is refused."""


class CadenceAdmissionRefusedProblem(ProblemDetails[Literal["urn:blizzard:problem:cadence-admission-refused"]]):
    """A write would leave a cadence promoting its runs, by a caller who may not admit work."""


class CadenceSlugTakenProblem(ProblemDetails[Literal["urn:blizzard:problem:cadence-slug-taken"]]):
    """The project already holds a cadence of that slug."""


class ScopeSlugInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:scope-slug-invalid"]]):
    """A scope slug is malformed."""


class RoutineDefinitionInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-definition-invalid"]]):
    """A routine write states a definition that cannot hold: a bad or taken slug, an unresolved graph, a scope of
    another project, invalid harnesses, an immutable slug changed, or the default scope unlinked."""


class RoutineScopeNotRelatedProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-scope-not-related"]]):
    """A run names a scope outside the routine's related set."""


class RoutineNotRunnableProblem(ProblemDetails[Literal["urn:blizzard:problem:routine-not-runnable"]]):
    """A routine run is refused: the routine or the effective scope is retired, or the graph has no enabled mint."""


class GardenWindowInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:garden-window-invalid"]]):
    """A time window is malformed: an instant that is not ISO-8601, or a span the window refuses."""


class GardenProposalAlreadyClosedProblem(
    ProblemDetails[Literal["urn:blizzard:problem:garden-proposal-already-closed"]]
):
    """The proposal already carries a closure, which is terminal."""


class GardenProposalInvalidProblem(ProblemDetails[Literal["urn:blizzard:problem:garden-proposal-invalid"]]):
    """A garden-proposal write states something that cannot hold: a blank or empty field, a body without a mint,
    or an unknown, duplicate, exited, foreign or mislinked finding."""


class GardenDefaultGraphRetiredProblem(ProblemDetails[Literal["urn:blizzard:problem:garden-default-graph-retired"]]):
    """The packaged default graph is retired, so accepting a proposal cannot mint its work item."""
