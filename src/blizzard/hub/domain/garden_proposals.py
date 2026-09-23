"""Garden-proposal domain model — a proposed response to one or more findings
(blizzard#390). Named `garden_proposals`/`GardenProposal` throughout — never the bare
`proposal`/`Proposal` a work-item proposal already claims (D1)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.findings import Finding

if TYPE_CHECKING:
    # Deferred to break the cycle: `garden_proposal_closure.py` itself imports
    # `GardenProposal` from this module.
    from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosure, IReadGardenProposalClosureRepository


class EmptyProposalFindingsError(ValueError):
    """A proposal names no findings (D7) — required and non-empty."""

    def __init__(self) -> None:
        super().__init__("a garden proposal must name at least one finding")


class DuplicateProposalFindingError(ValueError):
    """The same finding named more than once in one proposal's `findings`."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} named more than once")


@dataclass(frozen=True)
class GardenProposal:
    proposal_id: str
    routine_name: str
    class_: str
    title: str
    body: str
    created_at: datetime
    findings: list[str] = field(default_factory=list)  # the finding ids this proposal answers (D7)


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


@dataclass(frozen=True)
class GardenProposalPage:
    """A bounded, keyset-paginated page of :meth:`IReadGardenProposalRepository.list_page`
    (blizzard#526 D4) — ``next_cursor`` is ``None`` on the last one."""

    proposals: list[GardenProposal]
    next_cursor: str | None


class IReadGardenProposalRepository(Protocol):
    """Read-only garden-proposal access. Controllers at the edges depend on this variant."""

    def get(self, proposal_id: str) -> GardenProposal | None: ...

    def list_all(self) -> list[GardenProposal]: ...

    def list_page(self, *, cursor: str | None = None, limit: int) -> GardenProposalPage:
        """`list_all`'s bounded sibling, ordered ``(created_at desc, proposal_id desc)``
        (blizzard#526 D4). ``cursor`` is a prior :attr:`GardenProposalPage.next_cursor`;
        any other raises :class:`~blizzard.hub.domain.pagination.MalformedCursor`."""
        ...

    def list_for_routine(self, routine_name: str) -> list[GardenProposal]:
        """Every proposal `routine_name` has raised, newest first — `list_all`'s
        routine-narrowed sibling (mirrors `IReadFindingRepository.list_for_routine`). A
        proposal carries no scope column, so unlike a finding bucket this is never
        narrowed further."""
        ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs among `routine_name`'s proposals
        (blizzard-context:/domain/findings-and-proposals.md §`class` and `locus` are
        opaque) — a count, never the rows themselves."""
        ...


class IWriteGardenProposalRepository(IReadGardenProposalRepository, Protocol):
    """Read-write garden-proposal access. Only the domain layer depends on this variant."""

    def create(
        self,
        proposal_id: str,
        *,
        routine_name: str,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        """Insert the proposal row and its `garden_proposal_findings` link rows (D7), in
        one transaction. Non-empty `findings` is enforced by
        :class:`GardenProposalAuthoring` before this is called."""
        ...


class GardenProposalAuthoring:
    """Create a garden proposal from loaded findings (`bzh:domain-takes-objects`),
    rejecting an empty or duplicate-naming `findings` list (D7, blizzard#390)."""

    def __init__(self, *, proposals: IWriteGardenProposalRepository, clock: IClock) -> None:
        self._proposals = proposals
        self._clock = clock

    def create(
        self, *, routine_name: str, class_: str, title: str, body: str, findings: Sequence[Finding]
    ) -> GardenProposal:
        if not findings:
            raise EmptyProposalFindingsError()
        finding_ids = [f.finding_id for f in findings]
        seen: set[str] = set()
        for finding_id in finding_ids:
            if finding_id in seen:
                raise DuplicateProposalFindingError(finding_id)
            seen.add(finding_id)
        return self._proposals.create(
            Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            findings=finding_ids,
            at=self._clock.now(),
        )


class RoutineProposalState(StrEnum):
    """Which of a routine's garden proposals `OpenGardenProposalReader.list_for_routine`
    returns: `OPEN` (the default) excludes any proposal already closed, `CLOSED` returns
    only closed ones with their closure, `ALL` returns every proposal with its closure
    when one exists."""

    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class OpenGardenProposalReader:
    """A routine's garden proposals, filtered by `RoutineProposalState` and paired with
    each one's closure — `list_for_routine`'s own composed reader. Open means still
    awaiting a person's pass or accept."""

    def __init__(
        self, *, proposals: IReadGardenProposalRepository, closures: IReadGardenProposalClosureRepository
    ) -> None:
        self._proposals = proposals
        self._closures = closures

    def list_open_for_routine(self, routine_name: str) -> list[GardenProposal]:
        return [p for p, _ in self.list_for_routine(routine_name, RoutineProposalState.OPEN)]

    def list_for_routine(
        self, routine_name: str, state: RoutineProposalState = RoutineProposalState.OPEN
    ) -> list[tuple[GardenProposal, GardenProposalClosure | None]]:
        proposals = self._proposals.list_for_routine(routine_name)
        closures = self._closures.get_many([p.proposal_id for p in proposals])
        if state is RoutineProposalState.OPEN:
            return [(p, None) for p in proposals if p.proposal_id not in closures]
        if state is RoutineProposalState.CLOSED:
            return [(p, closures[p.proposal_id]) for p in proposals if p.proposal_id in closures]
        return [(p, closures.get(p.proposal_id)) for p in proposals]
