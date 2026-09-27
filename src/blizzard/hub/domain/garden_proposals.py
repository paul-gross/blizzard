"""Garden-proposal domain model —
`blizzard-context:/domain/findings-and-proposals.md` owns what a proposal is and
whether it needs a finding. Named `garden_proposals`/`GardenProposal` throughout —
never the bare `proposal`/`Proposal` a work-item proposal already claims."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.edit import UNSET, UnsetType
from blizzard.hub.domain.findings import Finding

if TYPE_CHECKING:
    # Deferred to break the cycle: `garden_proposal_closure.py` itself imports
    # `GardenProposal` from this module.
    from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosure, IReadGardenProposalClosureRepository
    from blizzard.hub.domain.routines import Routine


class DuplicateProposalFindingError(ValueError):
    """The same finding named more than once in one proposal's `findings`, or in one
    `attach`/`detach` call."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} named more than once")


class GardenProposalFindingNotLiveError(ValueError):
    """`create`/`attach` named a finding that is not live — the whole
    call is refused, nothing links."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} is not live")
        self.finding_id = finding_id


class GardenProposalFindingAlreadyLinkedError(ValueError):
    """`attach` named a finding already linked to *this* proposal —
    re-linking a finding already linked to *another* proposal, open or closed, is
    allowed."""

    def __init__(self, proposal_id: str, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} is already linked to proposal {proposal_id}")
        self.proposal_id = proposal_id
        self.finding_id = finding_id


class GardenProposalFindingNotLinkedError(ValueError):
    """`detach` named a finding not linked to this proposal."""

    def __init__(self, proposal_id: str, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} is not linked to proposal {proposal_id}")
        self.proposal_id = proposal_id
        self.finding_id = finding_id


class GardenProposalBlankFieldError(ValueError):
    """`create`/`create_operator`/`edit` named a blank title, class, or body —
    the whole call is refused."""

    def __init__(self, field_name: str) -> None:
        super().__init__(f"{field_name} must not be blank")
        self.field_name = field_name


class GardenProposalEmptyEditError(ValueError):
    """An `edit` named no field — every one of `GardenProposalEdit`'s fields left
    `UNSET`."""

    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"edit of garden proposal {proposal_id} must supply at least one field")
        self.proposal_id = proposal_id


class GardenProposalNotOpen(Exception):
    """An `edit`, `attach`, or `detach` targeted a proposal that already carries a
    closure — closure is terminal. Mirrors
    `garden_proposal_closure.GardenProposalAlreadyClosed` in spirit, but is raised from
    here without importing it, which would cycle back through `GardenProposal`."""

    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"garden proposal {proposal_id} already carries a closure")
        self.proposal_id = proposal_id


class GardenProposalOrigin(StrEnum):
    """Who authored a garden proposal — a mint-time fact, stored on
    the row itself and never inferred from a null `routine_name`."""

    ROUTINE_RUN = "routine-run"
    OPERATOR = "operator"


@dataclass(frozen=True)
class GardenProposal:
    proposal_id: str
    origin: GardenProposalOrigin
    routine_name: str | None
    class_: str
    title: str
    body: str
    created_at: datetime
    created_by: str | None = None  # set only for `GardenProposalOrigin.OPERATOR`
    findings: list[str] = field(default_factory=list)  # the finding ids this proposal answers


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


@dataclass(frozen=True)
class GardenProposalCounts:
    """One origin/routine/class triple's garden-proposal counts over a window:
    `open`, `passed`, `accepted_with_item`,
    `accepted_without_item` — each a closure-state bucket. `created` is always their
    sum, derived rather than stored, so it can never disagree with the four it sums."""

    origin: GardenProposalOrigin
    routine_name: str | None
    class_: str
    open: int
    passed: int
    accepted_with_item: int
    accepted_without_item: int

    @property
    def created(self) -> int:
        return self.open + self.passed + self.accepted_with_item + self.accepted_without_item


@dataclass(frozen=True)
class GardenProposalPage:
    """A bounded, keyset-paginated page of :meth:`IReadGardenProposalRepository.list_page`
    — ``next_cursor`` is ``None`` on the last one."""

    proposals: list[GardenProposal]
    next_cursor: str | None


class IReadGardenProposalRepository(Protocol):
    """Read-only garden-proposal access. Controllers at the edges depend on this variant."""

    def get(self, proposal_id: str) -> GardenProposal | None: ...

    def list_all(self) -> list[GardenProposal]: ...

    def list_page(
        self, *, cursor: str | None = None, limit: int, origin: GardenProposalOrigin | None = None
    ) -> GardenProposalPage:
        """`list_all`'s bounded sibling, ordered ``(created_at desc, proposal_id desc)``.
        ``cursor`` is a prior :attr:`GardenProposalPage.next_cursor`;
        any other raises :class:`~blizzard.hub.domain.pagination.MalformedCursor`.
        ``origin`` narrows to one origin when given, applied in SQL inside the keyset
        window."""
        ...

    def list_for_routine(self, routine_name: str) -> list[GardenProposal]:
        """Every proposal `routine_name` has raised, newest first — `list_all`'s
        routine-narrowed sibling (mirrors `IReadFindingRepository.list_for_routine`). A
        proposal carries no scope column, so unlike a finding bucket this is never
        narrowed further. Includes an `operator`-origin proposal that names
        `routine_name`."""
        ...

    def counts_by_class(
        self,
        *,
        since: datetime,
        until: datetime,
        routine_name: str | None = None,
        origin: GardenProposalOrigin | None = None,
    ) -> list[GardenProposalCounts]:
        """Garden-proposal counts grouped by `(origin, routine_name,
        class_)`, over `[since, until)` on `created_at` — current closure state, not
        closure time. `routine_name` narrows to one routine's rows of
        both origins when given, else every routine; `origin` narrows to one origin,
        applied in SQL. Rows ordered `(origin, routine_name, class_)`."""
        ...


class IWriteGardenProposalRepository(IReadGardenProposalRepository, Protocol):
    """Read-write garden-proposal access. Only the domain layer depends on this variant."""

    def create(
        self,
        proposal_id: str,
        *,
        origin: GardenProposalOrigin = GardenProposalOrigin.ROUTINE_RUN,
        routine_name: str | None,
        created_by: str | None = None,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        """Insert the proposal row and its `garden_proposal_findings` link rows, in one
        transaction. `findings` may be empty. `origin` defaults to `routine-run`,
        matching every caller predating operator authorship."""
        ...

    def edit(self, proposal_id: str, *, title: str, class_: str, body: str) -> GardenProposal | None:
        """Replace `title`/`class_`/`body` in place (last-write-wins, no
        edit-history table); `None` when `proposal_id` already carries a closure. Locks
        the proposal's own row before checking, so a close racing in cannot land in the
        gap (`GardenProposalStore._open_check`)."""
        ...

    def attach(self, proposal_id: str, finding_ids: Sequence[str]) -> GardenProposal | None:
        """Link `finding_ids` to `proposal_id`; `None` when already
        closed, the same row-locked check `edit` uses. `finding_ids` may be empty."""
        ...

    def detach(self, proposal_id: str, finding_ids: Sequence[str]) -> GardenProposal | None:
        """Unlink `finding_ids` from `proposal_id`; `None` when already
        closed, the same row-locked check `edit` uses. `finding_ids` may be empty."""
        ...


@dataclass(frozen=True)
class GardenProposalEdit:
    """The fields a single all-or-nothing garden-proposal edit request supplies,
    the same sentinel shape
    :class:`~blizzard.hub.domain.work_items.WorkItemEdit` carries: a field absent from
    `edit` is left unchanged, distinct from an explicit clear (title/class_/body never
    accept `None` — they are never cleared, only replaced)."""

    title: str | UnsetType = field(default=UNSET)
    class_: str | UnsetType = field(default=UNSET)
    body: str | UnsetType = field(default=UNSET)


class GardenProposalAuthoring:
    """Create, edit, attach to, or detach from a garden proposal, from already-loaded
    objects (`bzh:domain-takes-objects`) — the domain seam that mints and mutates
    proposals outside delivery. `create` mints a `routine-run`-origin proposal;
    delivery's own materialization writes its own `routine-run` rows directly and never
    calls this. `create_operator`/`edit`/`attach`/`detach` are the operator-facing verbs.
    Every refusal — closed-first, then blank fields or findings — is
    decided here, never left to a caller at the edge to enforce in the right order."""

    def __init__(
        self,
        *,
        proposals: IWriteGardenProposalRepository,
        closures: IReadGardenProposalClosureRepository,
        clock: IClock,
    ) -> None:
        self._proposals = proposals
        self._closures = closures
        self._clock = clock

    def create(
        self, *, routine_name: str, class_: str, title: str, body: str, findings: Sequence[Finding]
    ) -> GardenProposal:
        finding_ids = self._checked_finding_ids(findings, require_live=False)
        return self._proposals.create(
            Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
            origin=GardenProposalOrigin.ROUTINE_RUN,
            routine_name=routine_name,
            created_by=None,
            class_=class_,
            title=title,
            body=body,
            findings=finding_ids,
            at=self._clock.now(),
        )

    def create_operator(
        self,
        *,
        created_by: str,
        routine: Routine | None,
        class_: str,
        title: str,
        body: str,
        findings: Sequence[Finding],
    ) -> GardenProposal:
        """Mint an operator-authored proposal, naming `routine.name`
        when the caller resolved one, else none — `routine`'s own existence is the
        caller's own resolution, not checked here. `title`/`class_`/`body` must not be
        blank, and every named finding must be live and named at most once; the whole
        call is refused otherwise, nothing is linked."""
        title = self._stripped(title, "title")
        class_ = self._stripped(class_, "class")
        body = self._stripped(body, "body")
        finding_ids = self._checked_finding_ids(findings, require_live=True)
        return self._proposals.create(
            Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
            origin=GardenProposalOrigin.OPERATOR,
            routine_name=routine.name if routine is not None else None,
            created_by=created_by,
            class_=class_,
            title=title,
            body=body,
            findings=finding_ids,
            at=self._clock.now(),
        )

    def edit(self, proposal: GardenProposal, edit: GardenProposalEdit) -> GardenProposal:
        """Apply `edit`'s given fields to `proposal` in place. Raises
        :class:`GardenProposalNotOpen` first, ahead of any field problem, when `proposal`
        already carries a closure — re-checked by the store's own row-locked guard
        against a close racing in between — works on either origin while open. Raises
        :class:`GardenProposalEmptyEditError` when every field is left `UNSET`, or
        :class:`GardenProposalBlankFieldError` for a given field that is blank."""
        if self._closures.get(proposal.proposal_id) is not None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        if edit.title is UNSET and edit.class_ is UNSET and edit.body is UNSET:
            raise GardenProposalEmptyEditError(proposal.proposal_id)
        title = proposal.title if edit.title is UNSET else self._stripped(edit.title, "title")
        class_ = proposal.class_ if edit.class_ is UNSET else self._stripped(edit.class_, "class")
        body = proposal.body if edit.body is UNSET else self._stripped(edit.body, "body")
        updated = self._proposals.edit(proposal.proposal_id, title=title, class_=class_, body=body)
        if updated is None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        return updated

    def attach(self, proposal: GardenProposal, findings: Sequence[Finding]) -> GardenProposal:
        """Link `findings` to `proposal`. Raises
        :class:`GardenProposalNotOpen` first, ahead of any finding problem, when
        `proposal` already carries a closure. Every named finding must be live, named at
        most once, and not already linked to `proposal` — re-linking a finding already
        linked to *another* proposal is allowed."""
        if self._closures.get(proposal.proposal_id) is not None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        finding_ids = self._checked_finding_ids(findings, require_live=True)
        for finding_id in finding_ids:
            if finding_id in proposal.findings:
                raise GardenProposalFindingAlreadyLinkedError(proposal.proposal_id, finding_id)
        updated = self._proposals.attach(proposal.proposal_id, finding_ids)
        if updated is None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        return updated

    def detach(self, proposal: GardenProposal, finding_ids: Sequence[str]) -> GardenProposal:
        """Unlink `finding_ids` from `proposal`. Raises
        :class:`GardenProposalNotOpen` first, ahead of any finding problem, when
        `proposal` already carries a closure. Every named id must be named at most once
        and currently linked to `proposal`; liveness is not required."""
        if self._closures.get(proposal.proposal_id) is not None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        seen: set[str] = set()
        for finding_id in finding_ids:
            if finding_id in seen:
                raise DuplicateProposalFindingError(finding_id)
            seen.add(finding_id)
            if finding_id not in proposal.findings:
                raise GardenProposalFindingNotLinkedError(proposal.proposal_id, finding_id)
        updated = self._proposals.detach(proposal.proposal_id, list(finding_ids))
        if updated is None:
            raise GardenProposalNotOpen(proposal.proposal_id)
        return updated

    @staticmethod
    def _stripped(value: str, field_name: str) -> str:
        text = value.strip()
        if not text:
            raise GardenProposalBlankFieldError(field_name)
        return text

    def _checked_finding_ids(self, findings: Sequence[Finding], *, require_live: bool) -> list[str]:
        finding_ids: list[str] = []
        seen: set[str] = set()
        for finding in findings:
            if finding.finding_id in seen:
                raise DuplicateProposalFindingError(finding.finding_id)
            seen.add(finding.finding_id)
            if require_live and not finding.live:
                raise GardenProposalFindingNotLiveError(finding.finding_id)
            finding_ids.append(finding.finding_id)
        return finding_ids


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
