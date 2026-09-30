"""Garden-proposal domain model —
`blizzard-context:/domain/findings-and-proposals.md` owns what a proposal is and
whether it needs a finding. Named `garden_proposals`/`GardenProposal` throughout —
never the bare `proposal`/`Proposal` a work-item proposal already claims."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, NoReturn, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.edit import UNSET, UnsetType
from blizzard.hub.domain.findings import EXIT_KINDS, Finding

if TYPE_CHECKING:
    # Deferred to break the cycle: `garden_proposal_closure.py` itself imports
    # `GardenProposal` and `GardenProposalAlreadyClosed` from this module.
    from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosure, IReadGardenProposalClosureRepository
    from blizzard.hub.domain.routines import Routine


class DuplicateProposalFindingError(ValueError):
    """The same finding named more than once in one proposal's `findings`, or in one
    `attach`/`detach` call."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} named more than once")


class GardenProposalFindingExitedError(ValueError):
    """`create`/`attach` named an exited finding — the whole
    call is refused, nothing links."""

    def __init__(self, finding_id: str) -> None:
        super().__init__(f"finding {finding_id!r} has been exited")
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
    """`create_operator`/`edit` named a blank title, class, or body —
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


class GardenProposalAlreadyClosed(Exception):
    """A pass, accept, edit, attach, or detach targeted a proposal that already carries a
    closure — closure is terminal, so no verb is retroactive."""

    def __init__(self, proposal_id: str, closure: GardenProposalClosure) -> None:
        super().__init__(f"garden proposal {proposal_id} is already {closure.closure.value}")
        self.proposal_id = proposal_id
        self.closure = closure


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
        origin: GardenProposalOrigin,
        routine_name: str | None,
        created_by: str | None = None,
        class_: str,
        title: str,
        body: str,
        findings: list[str],
        at: datetime,
    ) -> GardenProposal:
        """Insert the proposal row and its `garden_proposal_findings` link rows, in one
        transaction. `findings` may be empty."""
        ...

    def edit(self, proposal_id: str, edit: GardenProposalEdit) -> GardenProposal | None:
        """Write only the fields `edit` gives, in place (no edit-history table), leaving
        every `UNSET` column as it stands; `None` when `proposal_id` already carries a
        closure. Locks the proposal's own row before checking, so a close racing in
        cannot land in the gap. `edit`'s given fields
        arrive already stripped and validated."""
        ...

    def attach(self, proposal_id: str, finding_ids: Sequence[str]) -> GardenProposal | None:
        """Link `finding_ids` to `proposal_id`; `None` when already closed, the same
        row-locked check `edit` uses. Raises :class:`GardenProposalFindingAlreadyLinkedError`
        when a finding is already linked, re-read under that same lock. `finding_ids` may
        be empty."""
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
    """The operator-facing verbs — `create_operator`, `edit`, `attach`, `detach` — over
    already-loaded objects (`bzh:domain-takes-objects`). Every refusal, closed first, is decided here, never
    left to a caller at the edge."""

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
        blank, and every named finding must be unexited and named at most once; the whole
        call is refused otherwise, nothing is linked."""
        title = self._stripped(title, "title")
        class_ = self._stripped(class_, "class")
        body = self._stripped(body, "body")
        finding_ids = self._checked_finding_ids(findings, require_unexited=True)
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
        """Apply only `edit`'s given fields, so a field it leaves out keeps a concurrent
        edit's value. Raises :class:`GardenProposalAlreadyClosed` first — re-checked by
        the store under its row lock — then :class:`GardenProposalEmptyEditError` when
        every field is `UNSET`, or :class:`GardenProposalBlankFieldError` for a blank one."""
        self._refuse_if_closed(proposal.proposal_id)
        if edit.title is UNSET and edit.class_ is UNSET and edit.body is UNSET:
            raise GardenProposalEmptyEditError(proposal.proposal_id)
        stripped = GardenProposalEdit(
            title=edit.title if edit.title is UNSET else self._stripped(edit.title, "title"),
            class_=edit.class_ if edit.class_ is UNSET else self._stripped(edit.class_, "class"),
            body=edit.body if edit.body is UNSET else self._stripped(edit.body, "body"),
        )
        updated = self._proposals.edit(proposal.proposal_id, stripped)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def attach(self, proposal: GardenProposal, findings: Sequence[Finding]) -> GardenProposal:
        """Link `findings` to `proposal`. Raises :class:`GardenProposalAlreadyClosed`
        first; every finding must be unexited, named once, and not already linked to
        `proposal` (re-made by the store under its row lock) — linked to *another*
        proposal is allowed."""
        self._refuse_if_closed(proposal.proposal_id)
        finding_ids = self._checked_finding_ids(findings, require_unexited=True)
        for finding_id in finding_ids:
            if finding_id in proposal.findings:
                raise GardenProposalFindingAlreadyLinkedError(proposal.proposal_id, finding_id)
        updated = self._proposals.attach(proposal.proposal_id, finding_ids)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def detach(self, proposal: GardenProposal, findings: Sequence[Finding]) -> GardenProposal:
        """Unlink `findings` from `proposal`. Raises
        :class:`GardenProposalAlreadyClosed` first, ahead of any finding problem, when
        `proposal` already carries a closure. Every named finding must be named at most
        once and currently linked to `proposal`; exit is not checked."""
        self._refuse_if_closed(proposal.proposal_id)
        finding_ids = self._checked_finding_ids(findings, require_unexited=False)
        for finding_id in finding_ids:
            if finding_id not in proposal.findings:
                raise GardenProposalFindingNotLinkedError(proposal.proposal_id, finding_id)
        updated = self._proposals.detach(proposal.proposal_id, finding_ids)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def _refuse_if_closed(self, proposal_id: str) -> None:
        closure = self._closures.get(proposal_id)
        if closure is not None:
            raise GardenProposalAlreadyClosed(proposal_id, closure)

    def _raise_already_closed(self, proposal_id: str) -> NoReturn:
        closure = self._closures.get(proposal_id)
        assert closure is not None
        raise GardenProposalAlreadyClosed(proposal_id, closure)

    @staticmethod
    def _stripped(value: str, field_name: str) -> str:
        text = value.strip()
        if not text:
            raise GardenProposalBlankFieldError(field_name)
        return text

    def _checked_finding_ids(self, findings: Sequence[Finding], *, require_unexited: bool) -> list[str]:
        finding_ids: list[str] = []
        seen: set[str] = set()
        for finding in findings:
            if finding.finding_id in seen:
                raise DuplicateProposalFindingError(finding.finding_id)
            seen.add(finding.finding_id)
            if require_unexited and finding.state in EXIT_KINDS:
                raise GardenProposalFindingExitedError(finding.finding_id)
            finding_ids.append(finding.finding_id)
        return finding_ids


class RoutineProposalState(StrEnum):
    """Which of a routine's garden proposals `RoutineGardenProposalReader.list_for_routine`
    returns: `OPEN` (the default) excludes any proposal already closed, `CLOSED` returns
    only closed ones with their closure, `ALL` returns every proposal with its closure
    when one exists."""

    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class RoutineGardenProposalReader:
    """A routine's garden proposals, filtered by `RoutineProposalState` and paired with
    each one's closure — `list_for_routine`'s own composed reader. Open means still
    awaiting a person's pass or accept."""

    def __init__(
        self, *, proposals: IReadGardenProposalRepository, closures: IReadGardenProposalClosureRepository
    ) -> None:
        self._proposals = proposals
        self._closures = closures

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
