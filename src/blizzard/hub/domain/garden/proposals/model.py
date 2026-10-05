"""Garden-proposal domain model —
`blizzard-context:/domain/findings-and-proposals.md` §A proposal's origin: a routine's
run, or an operator owns what a proposal is, and §A proposal's findings are optional
whether it needs a finding. Named `garden_proposals`/`GardenProposal` throughout —
never the bare `proposal`/`Proposal` a work-item proposal already claims."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, NoReturn, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.garden_proposals import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    GardenProposalOrigin,
)
from blizzard.foundation.ids import GARDEN_PROPOSAL_PREFIX, Id
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

if TYPE_CHECKING:
    # Deferred to break the cycle: `closure.py` itself imports
    # `GardenProposal` and `GardenProposalAlreadyClosed` from this module.
    from blizzard.hub.domain.garden.proposals.closure import GardenProposalClosure, IReadGardenProposalClosureRepository
    from blizzard.hub.domain.garden.routines import Routine


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


class GardenProposalNoFindingsError(ValueError):
    """An `attach`/`detach` named no finding — the same refusal an edit naming no field
    meets."""

    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"attach or detach on garden proposal {proposal_id} must name at least one finding")
        self.proposal_id = proposal_id


class GardenProposalAlreadyClosed(Exception):
    """A pass, accept, edit, attach, or detach targeted a proposal that already carries a
    closure — closure is terminal, so no verb is retroactive."""

    def __init__(self, proposal_id: str, closure: GardenProposalClosure) -> None:
        super().__init__(f"garden proposal {proposal_id} is already {closure.closure.value}")
        self.proposal_id = proposal_id
        self.closure = closure


class RoutineProposalState(StrEnum):
    """Select open, closed, or all of a routine's garden proposals."""

    OPEN = "open"
    CLOSED = "closed"
    ALL = "all"


class GardenProposalState(StrEnum):
    """A garden proposal's state, derived from its closure row (:func:`garden_proposal_state`)
    and never stored: open until a pass or an accept closes it, and every closed state
    terminal."""

    OPEN = "open"
    PASSED = "passed"
    ACCEPTED_MINTED = "accepted_minted"
    ACCEPTED_DECLINED = "accepted_declined"


class GardenProposalVerb(StrEnum):
    """The verbs that reach an existing garden proposal. ``DELIVER`` is the item an
    accepted, minting closure minted being delivered, which closes the proposal's
    still-live findings."""

    EDIT = "edit"
    ATTACH = "attach"
    DETACH = "detach"
    PASS = "pass"
    ACCEPT = "accept"
    DELIVER = "deliver"


#: Which verbs are legal from which state (``blizzard-context:/domain/findings-and-proposals.md``).
GARDEN_PROPOSAL_TRANSITIONS: Mapping[GardenProposalState, frozenset[GardenProposalVerb]] = {
    GardenProposalState.OPEN: frozenset(
        {
            GardenProposalVerb.EDIT,
            GardenProposalVerb.ATTACH,
            GardenProposalVerb.DETACH,
            GardenProposalVerb.PASS,
            GardenProposalVerb.ACCEPT,
        }
    ),
    GardenProposalState.PASSED: frozenset(),
    GardenProposalState.ACCEPTED_MINTED: frozenset({GardenProposalVerb.DELIVER}),
    GardenProposalState.ACCEPTED_DECLINED: frozenset(),
}


def garden_proposal_state(
    closure: GardenProposalClosureKind | None, item_outcome: GardenProposalItemOutcome | None
) -> GardenProposalState:
    """The state a closure row's kind and item outcome put its proposal in; no closure is
    `OPEN`. An `ACCEPTED` closure carrying no item outcome is refused rather than guessed."""
    if closure is None:
        return GardenProposalState.OPEN
    if closure is GardenProposalClosureKind.PASSED:
        return GardenProposalState.PASSED
    if item_outcome is GardenProposalItemOutcome.MINTED:
        return GardenProposalState.ACCEPTED_MINTED
    if item_outcome is GardenProposalItemOutcome.DECLINED:
        return GardenProposalState.ACCEPTED_DECLINED
    raise ValueError(f"accepted closure carries no item_outcome: {closure!r}")


def verb_legal_from(state: GardenProposalState, verb: GardenProposalVerb) -> bool:
    """Whether :data:`GARDEN_PROPOSAL_TRANSITIONS` allows `verb` from `state`."""
    return verb in GARDEN_PROPOSAL_TRANSITIONS[state]


def require_text(value: str, field_name: str) -> str:
    """`value` stripped; :class:`GardenProposalBlankFieldError` naming `field_name` when
    nothing is left. A proposal's title, class, and body are never blank, whichever door
    wrote them."""
    text = value.strip()
    if not text:
        raise GardenProposalBlankFieldError(field_name)
    return text


def citable_finding_ids(findings: Sequence[Finding], *, require_unexited: bool) -> list[str]:
    """The ids of `findings`, in order, refusing per finding a repeat
    (:class:`DuplicateProposalFindingError`) and then, when `require_unexited`, an exited
    one (:class:`GardenProposalFindingExitedError`). `delivered` and `gone` findings are
    citable — only an exit bars one."""
    finding_ids: list[str] = []
    seen: set[str] = set()
    for finding in findings:
        if finding.finding_id in seen:
            raise DuplicateProposalFindingError(finding.finding_id)
        seen.add(finding.finding_id)
        if require_unexited and finding.exited:
            raise GardenProposalFindingExitedError(finding.finding_id)
        finding_ids.append(finding.finding_id)
    return finding_ids


@domain_model
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

    @classmethod
    def operator(
        cls,
        proposal_id: str,
        *,
        created_by: str,
        routine_name: str | None,
        class_: str,
        title: str,
        body: str,
        findings: Sequence[Finding],
        at: datetime,
    ) -> GardenProposal:
        """An operator-authored proposal, its text stored stripped. Refuses a blank title,
        then class, then body, then a repeated or exited finding — the whole proposal,
        nothing linked."""
        title = require_text(title, "title")
        class_ = require_text(class_, "class")
        body = require_text(body, "body")
        finding_ids = citable_finding_ids(findings, require_unexited=True)
        return cls(
            proposal_id=proposal_id,
            origin=GardenProposalOrigin.OPERATOR,
            routine_name=routine_name,
            class_=class_,
            title=title,
            body=body,
            created_at=at,
            created_by=created_by,
            findings=finding_ids,
        )

    def require_legal(self, verb: GardenProposalVerb, closure: GardenProposalClosure | None) -> None:
        """Refuse `verb` with :class:`GardenProposalAlreadyClosed` when
        :data:`GARDEN_PROPOSAL_TRANSITIONS` does not allow it from the state `closure`
        puts this proposal in."""
        state = closure.state if closure is not None else GardenProposalState.OPEN
        if not verb_legal_from(state, verb):
            assert closure is not None  # every verb but DELIVER is legal while open
            raise GardenProposalAlreadyClosed(self.proposal_id, closure)

    def edit_fields(self, edit: GardenProposalEdit, *, closure: GardenProposalClosure | None) -> GardenProposalEdit:
        """`edit` with every given field stripped. Refuses a closed proposal first, then an
        edit naming no field, then a blank given field."""
        self.require_legal(GardenProposalVerb.EDIT, closure)
        return edit.normalized(self.proposal_id)

    def attachable(self, findings: Sequence[Finding], *, closure: GardenProposalClosure | None) -> list[str]:
        """The ids to link. Refuses a closed proposal first, then an empty list, then per
        finding a repeat or an exited one, then one already linked to this proposal —
        linked to another proposal, open or closed, is allowed."""
        self.require_legal(GardenProposalVerb.ATTACH, closure)
        if not findings:
            raise GardenProposalNoFindingsError(self.proposal_id)
        finding_ids = citable_finding_ids(findings, require_unexited=True)
        for finding_id in finding_ids:
            if finding_id in self.findings:
                raise GardenProposalFindingAlreadyLinkedError(self.proposal_id, finding_id)
        return finding_ids

    def detachable(self, findings: Sequence[Finding], *, closure: GardenProposalClosure | None) -> list[str]:
        """The ids to unlink. Refuses a closed proposal first, then an empty list, then a
        repeat, then one not linked to this proposal; an exited finding detaches."""
        self.require_legal(GardenProposalVerb.DETACH, closure)
        if not findings:
            raise GardenProposalNoFindingsError(self.proposal_id)
        finding_ids = citable_finding_ids(findings, require_unexited=False)
        for finding_id in finding_ids:
            if finding_id not in self.findings:
                raise GardenProposalFindingNotLinkedError(self.proposal_id, finding_id)
        return finding_ids


def pair_with_closures(
    proposals: Sequence[GardenProposal],
    closures: Mapping[str, GardenProposalClosure],
    state: RoutineProposalState,
) -> list[tuple[GardenProposal, GardenProposalClosure | None]]:
    """Each of `proposals` paired with its closure from `closures`, in order: `OPEN` pairs
    every one with none, `CLOSED` keeps only those carrying a closure, `ALL` keeps every
    one."""
    if state is RoutineProposalState.OPEN:
        return [(p, None) for p in proposals]
    if state is RoutineProposalState.CLOSED:
        return [(p, closures[p.proposal_id]) for p in proposals if p.proposal_id in closures]
    return [(p, closures.get(p.proposal_id)) for p in proposals]


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


@domain_model
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


@domain_model
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
        any other raises :class:`~blizzard.hub.domain.kernel.pagination.MalformedCursor`.
        ``origin`` narrows to one origin when given, applied in SQL inside the keyset
        window."""
        ...

    def list_for_routine(
        self, routine_name: str, *, state: RoutineProposalState = RoutineProposalState.ALL
    ) -> list[GardenProposal]:
        """Every proposal `routine_name` has raised, newest first — `list_all`'s
        routine-narrowed sibling (mirrors `IReadFindingRepository.list_for_routine`). A
        proposal carries no scope column, so unlike a finding bucket this is never
        narrowed further. Includes an `operator`-origin proposal that names
        `routine_name`. ``OPEN``/``CLOSED`` select closure state before hydrating
        proposal bodies and linked findings; ``ALL`` reads the full history."""
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


@domain_model
@dataclass(frozen=True)
class GardenProposalEdit:
    """The fields a single all-or-nothing garden-proposal edit request supplies,
    the same sentinel shape
    :class:`~blizzard.hub.domain.work_items.editing.WorkItemEdit` carries: a field absent from
    `edit` is left unchanged, distinct from an explicit clear (title/class_/body never
    accept `None` — they are never cleared, only replaced)."""

    title: str | UnsetType = field(default=UNSET)
    class_: str | UnsetType = field(default=UNSET)
    body: str | UnsetType = field(default=UNSET)

    def normalized(self, proposal_id: str) -> GardenProposalEdit:
        """This edit with every given field stripped. Refuses an edit naming no field
        (:class:`GardenProposalEmptyEditError`), then a blank given field, title before
        class before body (:class:`GardenProposalBlankFieldError`)."""
        if self.title is UNSET and self.class_ is UNSET and self.body is UNSET:
            raise GardenProposalEmptyEditError(proposal_id)
        return GardenProposalEdit(
            title=self.title if self.title is UNSET else require_text(self.title, "title"),
            class_=self.class_ if self.class_ is UNSET else require_text(self.class_, "class"),
            body=self.body if self.body is UNSET else require_text(self.body, "body"),
        )


class GardenProposalAuthoring:
    """The operator-facing verbs — `create_operator`, `edit`, `attach`, `detach` — over already-loaded
    objects. Every refusal is :class:`GardenProposal`'s own; this service loads the closure, mints the id
    and instant, writes, and turns a close that raced in under the row lock into
    :class:`GardenProposalAlreadyClosed`."""

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
        """Mint an operator-authored proposal (:meth:`GardenProposal.operator`), naming
        `routine.name` when the caller resolved one, else none — `routine`'s own
        existence is the caller's own resolution, not checked here."""
        at = self._clock.now()
        draft = GardenProposal.operator(
            Id.mint_at(GARDEN_PROPOSAL_PREFIX, at).value,
            created_by=created_by,
            routine_name=routine.name if routine is not None else None,
            class_=class_,
            title=title,
            body=body,
            findings=findings,
            at=at,
        )
        return self._proposals.create(
            draft.proposal_id,
            origin=draft.origin,
            routine_name=draft.routine_name,
            created_by=draft.created_by,
            class_=draft.class_,
            title=draft.title,
            body=draft.body,
            findings=draft.findings,
            at=draft.created_at,
        )

    def edit(self, proposal: GardenProposal, edit: GardenProposalEdit) -> GardenProposal:
        """Apply only `edit`'s given fields (:meth:`GardenProposal.edit_fields`), so a
        field it leaves out keeps a concurrent edit's value."""
        fields = proposal.edit_fields(edit, closure=self._closures.get(proposal.proposal_id))
        updated = self._proposals.edit(proposal.proposal_id, fields)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def attach(self, proposal: GardenProposal, findings: Sequence[Finding]) -> GardenProposal:
        """Link `findings` to `proposal` (:meth:`GardenProposal.attachable`); the store
        re-makes the already-linked check under its row lock."""
        finding_ids = proposal.attachable(findings, closure=self._closures.get(proposal.proposal_id))
        updated = self._proposals.attach(proposal.proposal_id, finding_ids)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def detach(self, proposal: GardenProposal, findings: Sequence[Finding]) -> GardenProposal:
        """Unlink `findings` from `proposal` (:meth:`GardenProposal.detachable`)."""
        finding_ids = proposal.detachable(findings, closure=self._closures.get(proposal.proposal_id))
        updated = self._proposals.detach(proposal.proposal_id, finding_ids)
        if updated is None:
            self._raise_already_closed(proposal.proposal_id)
        return updated

    def _raise_already_closed(self, proposal_id: str) -> NoReturn:
        closure = self._closures.get(proposal_id)
        assert closure is not None
        raise GardenProposalAlreadyClosed(proposal_id, closure)


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
        # Reconciliation needs every open proposal, without an arbitrary limit.
        # Closed/all are explicit history reads for comparing previously declined ideas.
        proposals = self._proposals.list_for_routine(routine_name, state=state)
        if state is RoutineProposalState.OPEN:
            return pair_with_closures(proposals, {}, state)
        return pair_with_closures(proposals, self._closures.get_many([p.proposal_id for p in proposals]), state)
