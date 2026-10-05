"""Finding domain model — a durable observation a routine's run recorded.

The no-stored-column contract is `src/blizzard/hub/store/schema.py`'s own.
Liveness is a derived fold over facts, reversible only by a person's own verb once
exited (blizzard-context:/domain/findings-and-proposals.md §Liveness is derived, and
reversible); `class_`/`locus` are opaque to the hub, same doc."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.findings import FindingExit, FindingFactKind, FindingState
from blizzard.foundation.roles import domain_model, dto

FACT_KINDS = frozenset(FindingFactKind)

#: The human-driven verbs that exit a finding for good; `reopened` is excluded since it undoes one.
EXIT_KINDS = frozenset(
    {
        FindingFactKind.RESOLVED,
        FindingFactKind.GONE_CONFIRMED,
        FindingFactKind.WONT_FIX,
        FindingFactKind.NOT_A_FINDING,
        FindingFactKind.SUPERSEDED,
    }
)

#: The fact kinds whose being newest makes a finding live — the one home of the liveness mapping,
#: shared by `derive_liveness` and the store's SQL prefilter.
LIVE_KINDS = frozenset({FindingFactKind.ADD, FindingFactKind.OBSERVED, FindingFactKind.REOPENED})

#: The ground itself changed — work landed, or a person confirmed non-reproduction.
OUTFLOW_KINDS = frozenset({FindingFactKind.RESOLVED, FindingFactKind.GONE_CONFIRMED})

#: A judgment call about the finding, not the code.
WITHDRAWN_KINDS = EXIT_KINDS - OUTFLOW_KINDS


class UnknownFactKindError(ValueError):
    """A `finding_facts` row named a `kind` outside `FACT_KINDS` — refused at the
    write path so `derive_liveness`'s newest-fact-wins fold, which assumes every kind is
    one of the nine, never has to reason about a stray value."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"unknown finding-fact kind {kind!r}")


class FindingNoteRequiredError(ValueError):
    """An exit or `reopened` fact carried a blank or missing note — every
    human-driven verb wants one, the way `gone`'s own note already does."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"{kind!r} requires a non-empty note")


class FindingSupersedesItself(ValueError):
    """A `superseded` fact named its own finding as the absorber — the absorber is never
    the finding being exited (blizzard-context:/domain/findings-and-proposals.md
    §Liveness is derived, and reversible)."""

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(f"finding {finding_id!r} cannot supersede itself")


class AbsorberNotLive(ValueError):
    """A `superseded` fact named an absorber that is not itself live — same doc, same
    section."""

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(f"finding {finding_id!r} is not live and cannot absorb another finding")


class DuplicateFindingError(ValueError):
    """One person's bulk verb named the same finding twice — refused rather than written
    as two identical facts the trend would count twice."""

    def __init__(self, finding_id: str) -> None:
        self.finding_id = finding_id
        super().__init__(f"finding {finding_id!r} is named more than once")


class FindingTransitionRefused(ValueError):
    """A verb applied to a finding whose state does not allow it, per
    :data:`FINDING_TRANSITIONS` — the base every state refusal shares."""

    def __init__(self, finding_id: str, kind: str, state: str, message: str) -> None:
        self.finding_id = finding_id
        self.kind = kind
        self.state = state
        super().__init__(message)


class FindingAlreadyExited(FindingTransitionRefused):
    """An exit verb on a finding already exited — a person reopens it first, so the
    re-classification leaves its own trail instead of silently rewriting the exit."""

    def __init__(self, finding_id: str, kind: str, state: str) -> None:
        super().__init__(
            finding_id, kind, state, f"finding {finding_id!r} is already {state}; reopen it before {kind!r}"
        )


class FindingWriteContended(ValueError):
    """A finding verb whose write kept losing its state guard to concurrent writes."""

    def __init__(self, finding_ids: Sequence[str]) -> None:
        self.finding_ids = list(finding_ids)
        super().__init__(f"findings {', '.join(self.finding_ids)} kept changing under the write; retry")


class FindingNotReopenable(FindingTransitionRefused):
    """`reopened` on a live finding — there is no exit, `gone`, or delivery to undo."""

    def __init__(self, finding_id: str, kind: str, state: str) -> None:
        super().__init__(finding_id, kind, state, f"finding {finding_id!r} is already live; nothing to reopen")


def require_note(kind: str, note: str | None) -> str:
    """`note` stripped — every human-driven verb, and `delivered`, wants a non-blank one."""
    stripped = (note or "").strip()
    if not stripped:
        raise FindingNoteRequiredError(kind)
    return stripped


#: The unexited states (`derive_liveness`): live, a run's provisional `gone`, a delivery's provisional `delivered`.
UNEXITED_STATES = frozenset({"live", "gone", "delivered"})
FINDING_STATES = UNEXITED_STATES | EXIT_KINDS

#: Which fact kind (the verb that writes it) is legal from which state; `add` mints and has no prior state.
FINDING_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "observed": UNEXITED_STATES,
    "gone": UNEXITED_STATES,
    "delivered": frozenset({"live"}),
    **dict.fromkeys(sorted(EXIT_KINDS), UNEXITED_STATES),
    "reopened": FINDING_STATES - {"live"},
}


@domain_model
@dataclass(frozen=True)
class Finding:
    finding_id: str
    #: A routine's own name, not its surrogate id; `None` for `source="review"`.
    routine_name: str | None
    scope_slug: str
    class_: str
    locus: str
    summary: str
    introduced: str | None
    #: The `introduced` commit's authored instant; null wherever unresolved, never backfilled.
    introduced_at: datetime | None
    #: When the garden first saw this finding (the `add` fact's instant), not when the commit landed (`introduced_at`).
    first_observed_at: datetime | None
    #: schema.py's `findings` table carries no such column.
    live: bool
    #: "live", "gone", "delivered", or one of `EXIT_KINDS` — the newest fact's own kind.
    state: str
    #: The newest fact's own note; `None` for a kind that carries none.
    note: str | None
    last_seen_at: datetime | None
    observed_count: int
    #: "routine" or "review" — a finding's home, not its liveness.
    source: str = "routine"
    #: blocking/should-fix; `None` for a routine-sourced finding.
    severity: str | None = None
    #: The chunk whose review raised this finding; `None` for a routine-sourced finding.
    raised_by_chunk_id: str | None = None
    #: The newest fact's own actor — a `delivered` finding's own closer.
    actor: str | None = None

    @property
    def exited(self) -> bool:
        """Left the live set for good, until a person reopens it."""
        return self._in_exit_state()

    @property
    def delivered(self) -> bool:
        """Closed by a delivery, provisionally, until its routine's next run settles it."""
        return self._in_delivered_state()

    def _in_exit_state(self) -> bool:
        return self.state in EXIT_KINDS

    def _in_delivered_state(self) -> bool:
        return self.state == "delivered"

    def allows(self, kind: str) -> bool:
        """Whether a `kind` fact is legal from this finding's state, per :data:`FINDING_TRANSITIONS`."""
        return self.state in FINDING_TRANSITIONS.get(kind, frozenset())

    def exit_fact(self, kind: str, *, note: str, actor: str, at: datetime, proposal_id: str | None = None) -> FactEntry:
        """A person's exit (any of `EXIT_KINDS` but `superseded`) or `reopened` fact — refused
        from a state :data:`FINDING_TRANSITIONS` disallows, then for a blank note."""
        if kind not in EXIT_KINDS - {"superseded"} and kind != "reopened":
            raise UnknownFactKindError(kind)
        self.check(kind)
        return FactEntry(
            finding_id=self.finding_id,
            kind=kind,
            at=at,
            note=require_note(kind, note),
            actor=actor,
            proposal_id=proposal_id,
        )

    def supersede_into(self, absorber: Finding, *, note: str, actor: str, at: datetime) -> FactEntry:
        """This finding's `superseded` fact, folded into `absorber` — which is never this
        finding and must itself be live."""
        if absorber.finding_id == self.finding_id:
            raise FindingSupersedesItself(self.finding_id)
        if not absorber.live:
            raise AbsorberNotLive(absorber.finding_id)
        self.check("superseded")
        return FactEntry(
            finding_id=self.finding_id,
            kind="superseded",
            at=at,
            note=require_note("superseded", note),
            actor=actor,
            superseded_by=absorber.finding_id,
        )

    def deliver_fact(self, *, note: str, actor: str, at: datetime, proposal_id: str | None = None) -> FactEntry | None:
        """The `delivered` fact a landed proposal closes this finding with, or `None` when
        it is no longer live — a finding a run has since reported gone, or a person has
        already exited, is left exactly as it stands
        (blizzard-context:/domain/findings-and-proposals.md §Closing a proposal)."""
        note = require_note("delivered", note)
        if not self.allows("delivered"):
            return None
        return FactEntry(
            finding_id=self.finding_id, kind="delivered", at=at, note=note, actor=actor, proposal_id=proposal_id
        )

    def run_gone(self) -> tuple[str, str | None]:
        """The fact kind and actor a run's `gone` op lands as: a `delivered` finding settles
        to `resolved`, carrying its closer as the actor; any other is flagged `gone`."""
        if self.delivered:
            return "resolved", self.actor
        return "gone", None

    def check(self, kind: str) -> None:
        """Refuse a `kind` fact this finding's state disallows — `reopened` on a live finding,
        any other verb on an exited one."""
        if self.allows(kind):
            return
        if kind == "reopened":
            raise FindingNotReopenable(self.finding_id, kind, self.state)
        raise FindingAlreadyExited(self.finding_id, kind, self.state)


@domain_model
@dataclass(frozen=True)
class FindingFact:
    """One `add`/`observed`/`gone`/`delivered`/exit/`reopened` transformation
    — append-only, oldest first."""

    kind: str
    recorded_at: datetime
    note: str | None = None
    #: Who recorded a human-driven fact; `None` for a run-driven `add`/`observed`/`gone`.
    actor: str | None = None
    #: The proposal a `delivered` fact answered, when the drain recorded it.
    proposal_id: str | None = None
    #: The absorbing finding, set only on a `superseded` fact.
    superseded_by: str | None = None


@domain_model
@dataclass(frozen=True)
class FindingLiveness:
    """The newest-fact-wins read over a finding's facts — never
    persisted."""

    state: FindingState
    live: bool
    note: str | None
    first_observed_at: datetime | None
    last_seen_at: datetime | None
    observed_count: int
    actor: str | None = None


def derive_liveness(facts: Sequence[FindingFact]) -> FindingLiveness:
    """The newest-fact-wins read over a finding's facts: any later
    fact reverses `gone` or `delivered`, but only `reopened` reverses an
    `EXIT_KINDS` verb. `first_observed_at`/`last_seen_at` are the min/max of the same
    `add`/`observed` span and use `recorded_at`, not insertion order, so out-of-order
    ingestion still derives correctly."""
    if not facts:
        return FindingLiveness(
            state=FindingState.LIVE, live=True, note=None, first_observed_at=None, last_seen_at=None, observed_count=0
        )
    seen = [f for f in facts if f.kind in ("add", "observed")]
    newest = facts[0]
    for fact in facts[1:]:
        if fact.recorded_at >= newest.recorded_at:  # a tie keeps the later-inserted fact
            newest = fact
    state = FindingState.LIVE if newest.kind in LIVE_KINDS else FindingState(newest.kind)
    return FindingLiveness(
        state=state,
        live=state is FindingState.LIVE,
        note=newest.note,
        first_observed_at=min((f.recorded_at for f in seen), default=None),
        last_seen_at=max((f.recorded_at for f in seen), default=None),
        observed_count=sum(1 for f in facts if f.kind == "observed"),
        actor=newest.actor,
    )


def finding_exit(state: str) -> FindingExit | None:
    """How a finding in ``state`` exited — ``outflow`` for an `OUTFLOW_KINDS` exit, ``withdrawn``
    for a `WITHDRAWN_KINDS` one, ``None`` for a finding that has not exited."""
    if state in OUTFLOW_KINDS:
        return FindingExit.OUTFLOW
    if state in WITHDRAWN_KINDS:
        return FindingExit.WITHDRAWN
    return None


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


@dto
@dataclass(frozen=True)
class FindingPage:
    """A bounded, keyset-paginated page of :meth:`IReadFindingRepository.list_page`
    — ``next_cursor`` is ``None`` exactly when this page is the last one."""

    findings: list[Finding]
    next_cursor: str | None


class IReadFindingRepository(Protocol):
    """Read-only finding access. Controllers at the edges depend on this variant.

    An unsettled `delivered` finding carries no staleness bound of its own — outside
    `include_gone=False` and every trend count until its owning routine revives or settles it."""

    def get(self, finding_id: str) -> Finding | None: ...

    def get_many(self, finding_ids: Sequence[str]) -> dict[str, Finding]:
        """`get`'s batched sibling, keyed by `finding_id` — a bulk exit verb's read side,
        so it costs one query pair, not one pair per row."""
        ...

    def get_with_facts(self, finding_id: str) -> tuple[Finding, list[FindingFact]] | None:
        """A finding and its whole fact chain, oldest-first, read together in one
        transaction, so a concurrent write between the two reads can never leave them
        disagreeing on the same instant. `None` for an unknown id, `get`'s own contract."""
        ...

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """A routine's findings under one scope
        (blizzard-product:/delivered/garden/machinery.md §Managing findings and proposals) —
        live only, unless `include_gone`, which also surfaces every exited finding,
        not just a merely `gone` one."""
        ...

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        """Every finding live on `routine_name`, across every scope it holds
        — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter.
        Live only, unless `include_gone`, which also surfaces every exited finding."""
        ...

    def list_across_routines(self, scope_slug: str | None = None, *, include_gone: bool = False) -> list[Finding]:
        """Every finding across every routine; `scope_slug=None` reads every
        scope. Table-scans by construction — `ix_findings_routine_scope` and
        `ix_findings_routine_class` both lead with `routine_name`, which this never filters
        on, so neither is usable; the scale question here stays open."""
        ...

    def list_by_source(self, *, scope_slug: str, source: str, include_gone: bool = False) -> list[Finding]:
        """Every finding under `scope_slug` carrying `source` — filtered
        on `ix_findings_scope_source`, indexed unlike `list_across_routines`."""
        ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name`
        (blizzard-product:/delivered/garden/machinery.md §What the store buys) — a count,
        never the rows themselves."""
        ...

    def list_page(
        self,
        *,
        routine_name: str | None,
        scope_slug: str | None,
        source: str | None = None,
        include_gone: bool = False,
        cursor: str | None = None,
        limit: int,
    ) -> FindingPage:
        """Bounded, keyset-paginated read unifying `list_for`/`list_for_routine`/
        `list_across_routines`; `source` narrows to `"routine"` or
        `"review"`, `None` reads both. Statements and rows read are set by the page, not by
        how many findings have exited: `include_gone=False` drops non-live findings in the
        query itself. `cursor` is a prior :attr:`FindingPage.next_cursor`."""
        ...

    def has_delivery_for_proposal(self, proposal_id: str) -> bool:
        """Whether any fact already carries `proposal_id` — kind-agnostic, and
        independent of any one finding's current state, so a later reopen is never
        silently redone."""
        ...


class IWriteFindingRepository(IReadFindingRepository, Protocol):
    """Read-write finding access. Only the domain layer depends on this variant."""

    def add(
        self,
        finding_id: str,
        *,
        routine_name: str,
        scope_slug: str,
        class_: str,
        locus: str,
        summary: str,
        introduced: str | None,
        at: datetime,
    ) -> Finding:
        """Insert the finding row and its own `add` fact, in one transaction."""
        ...

    def record_fact(
        self,
        finding_id: str,
        *,
        kind: str,
        at: datetime,
        note: str | None = None,
        actor: str | None = None,
        proposal_id: str | None = None,
        superseded_by: str | None = None,
    ) -> None:
        """Append one fact — never touches the `findings` row."""
        ...

    def record_facts(self, entries: Sequence[FactEntry], *, expect: Mapping[str, str] | None = None) -> list[str]:
        """All-or-nothing — pinned by
        `tests/test_finding_store.py::test_record_facts_is_all_or_nothing`. ``expect`` maps a
        finding id to the state its facts were built from; when any named finding has moved off
        it by the time the write lands, nothing is written and those ids are returned."""
        ...


@dto
@dataclass(frozen=True)
class FactEntry:
    """One `record_facts` row — the bulk-write shape for a single finding's fact."""

    finding_id: str
    kind: str
    at: datetime
    note: str | None
    actor: str | None = None
    proposal_id: str | None = None
    superseded_by: str | None = None


class IFindingExitResolver(Protocol):
    """Closes findings on a delivered proposal, recording `note` and `actor` as each
    finding's fact; not an exit itself."""

    def deliver(
        self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None
    ) -> None: ...


def refuse_duplicates(findings: Sequence[Finding]) -> None:
    """Refuse a person's bulk verb naming one finding twice."""
    seen: set[str] = set()
    for finding in findings:
        if finding.finding_id in seen:
            raise DuplicateFindingError(finding.finding_id)
        seen.add(finding.finding_id)


def exit_facts(
    findings: Sequence[Finding], kind: str, *, note: str, actor: str, at: datetime, proposal_id: str | None = None
) -> list[FactEntry]:
    """One person's bulk exit or `reopened` — every finding's fact, or the batch's refusal:
    a duplicate id, then any finding's state, then a blank note. Nothing is written for a
    refused batch."""
    refuse_duplicates(findings)
    for finding in findings:
        finding.check(kind)
    note = require_note(kind, note)
    return [f.exit_fact(kind, note=note, actor=actor, at=at, proposal_id=proposal_id) for f in findings]


def supersede_facts(
    findings: Sequence[Finding], absorber: Finding, *, note: str, actor: str, at: datetime
) -> list[FactEntry]:
    """One person's bulk `superseded` into `absorber` — `exit_facts`' shape, refusing a
    self-naming or non-live absorber before any finding's state."""
    refuse_duplicates(findings)
    for finding in findings:
        if absorber.finding_id == finding.finding_id:
            raise FindingSupersedesItself(finding.finding_id)
    if not absorber.live:
        raise AbsorberNotLive(absorber.finding_id)
    for finding in findings:
        finding.check("superseded")
    note = require_note("superseded", note)
    return [f.supersede_into(absorber, note=note, actor=actor, at=at) for f in findings]


def deliver_facts(
    findings: Sequence[Finding], *, note: str, actor: str, at: datetime, proposal_id: str | None = None
) -> list[FactEntry]:
    """A landed proposal's `delivered` facts — its still-live findings only, every other
    left as it stands (:meth:`Finding.deliver_fact`)."""
    note = require_note("delivered", note)
    facts = (f.deliver_fact(note=note, actor=actor, at=at, proposal_id=proposal_id) for f in findings)
    return [fact for fact in facts if fact is not None]


#: How many times a finding verb re-reads and re-asks the model after losing its state guard.
_GUARD_ATTEMPTS = 3


class FindingExitService:
    """The human-driven exit verbs, `reopen`, and the provisional, delivery-triggered `deliver`, over
    already-loaded :class:`Finding` objects; each writes the batch's facts in one all-or-nothing
    `record_facts` guarded on the states they were built from, re-asking the model when a concurrent
    write wins the guard."""

    def __init__(self, *, repo: IWriteFindingRepository, clock: IClock) -> None:
        self._repo = repo
        self._clock = clock

    def resolve(self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None) -> None:
        self._exit(findings, kind="resolved", note=note, actor=actor, proposal_id=proposal_id)

    def deliver(self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None) -> None:
        """Delivery-triggered closure — `resolved`'s provisional sibling:
        the owning routine's next run re-checks a `delivered` finding, settling it to
        `resolved` if it still holds or reviving it to `live` if it does not, rather than
        a delivery alone declaring the ground changed."""
        at = self._clock.now()
        self._record(
            findings, lambda batch: deliver_facts(batch, note=note, actor=actor, at=at, proposal_id=proposal_id)
        )

    def confirm_gone(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._exit(findings, kind="gone-confirmed", note=note, actor=actor)

    def wont_fix(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._exit(findings, kind="wont-fix", note=note, actor=actor)

    def not_a_finding(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._exit(findings, kind="not-a-finding", note=note, actor=actor)

    def supersede(self, findings: Sequence[Finding], absorber: Finding, *, note: str, actor: str) -> None:
        at = self._clock.now()
        self._record(findings, lambda batch: supersede_facts(batch, absorber, note=note, actor=actor, at=at))

    def reopen(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._exit(findings, kind="reopened", note=note, actor=actor)

    def _exit(
        self, findings: Sequence[Finding], *, kind: str, note: str, actor: str, proposal_id: str | None = None
    ) -> None:
        at = self._clock.now()
        self._record(
            findings, lambda batch: exit_facts(batch, kind, note=note, actor=actor, at=at, proposal_id=proposal_id)
        )

    def _record(self, findings: Sequence[Finding], facts_for: Callable[[Sequence[Finding]], list[FactEntry]]) -> None:
        batch = list(findings)
        for _ in range(_GUARD_ATTEMPTS):
            if not self._repo.record_facts(facts_for(batch), expect={f.finding_id: f.state for f in batch}):
                return
            reloaded = self._repo.get_many([f.finding_id for f in batch])
            batch = [reloaded.get(f.finding_id, f) for f in batch]
        facts_for(batch)
        raise FindingWriteContended([f.finding_id for f in batch])


@domain_model
@dataclass(frozen=True)
class FindingSet:
    """The set a delivered finding list mints, one per artifact — scope, the
    per-repository revisions, and the routine's measurement live here, never per finding."""

    finding_set_id: str
    artifact_id: str
    chunk_id: str
    scope_slug: str
    routine_name: str  # a routine's own name, not its surrogate id
    revisions: dict[str, str]
    measurement: str | None


class IReadFindingSetRepository(Protocol):
    """Read-only finding-set access. Controllers at the edges depend on this variant."""

    def get(self, finding_set_id: str) -> FindingSet | None: ...

    def list_for_chunk(self, chunk_id: str) -> list[FindingSet]:
        """A run's own delivered sets — filtered on `ix_finding_sets_chunk_id`."""
        ...

    def newest_for_routine_scope(self, routine_name: str, scope_slug: str) -> FindingSet | None:
        """A routine run's own delta baseline — the newest set for the
        (routine name, scope slug) pair, or `None` when the pair has recorded none.
        `finding_sets` carries no timestamp, so newest is `finding_set_id` descending:
        `fins_<ULID>` is monotonic in mint instant."""
        ...

    def newest_by_scope_for_routine(self, routine_name: str) -> list[FindingSet]:
        """One entry per scope `routine_name` has swept — the newest set for each,
        `newest_for_routine_scope`'s own batched sibling. A scope this routine has never
        swept is simply absent, never a `None` placeholder."""
        ...


class IWriteFindingSetRepository(IReadFindingSetRepository, Protocol):
    """Read-write finding-set access. Only the domain layer depends on this variant."""

    def create(
        self,
        finding_set_id: str,
        *,
        artifact_id: str,
        chunk_id: str,
        scope_slug: str,
        routine_name: str,
        revisions: dict[str, str],
        measurement: str | None,
    ) -> FindingSet:
        """Insert the set row — one per artifact (its own `uq`-backed unique FK)."""
        ...
