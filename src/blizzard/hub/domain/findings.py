"""Finding domain model — a durable observation a routine's run recorded.

The no-stored-column contract is `src/blizzard/hub/store/schema.py`'s own.
Liveness is a derived fold over facts, reversible only by a person's own verb once
exited (blizzard-context:/domain/findings-and-proposals.md §Liveness is derived, and
reversible); `class_`/`locus` are opaque to the hub, same doc."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock

FACT_KINDS = frozenset(
    {
        "add",
        "observed",
        "gone",
        "delivered",
        "resolved",
        "gone-confirmed",
        "wont-fix",
        "not-a-finding",
        "superseded",
        "reopened",
    }
)

#: The human-driven verbs that exit a finding for good; `reopened` is excluded since it undoes one.
EXIT_KINDS = frozenset({"resolved", "gone-confirmed", "wont-fix", "not-a-finding", "superseded"})

#: The ground itself changed — work landed, or a person confirmed non-reproduction.
OUTFLOW_KINDS = frozenset({"resolved", "gone-confirmed"})

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


@dataclass(frozen=True)
class FindingLiveness:
    """The newest-fact-wins read over a finding's facts — never
    persisted."""

    state: str
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
            state="live", live=True, note=None, first_observed_at=None, last_seen_at=None, observed_count=0
        )
    seen = [f for f in facts if f.kind in ("add", "observed")]
    newest = facts[0]
    for fact in facts[1:]:
        if fact.recorded_at >= newest.recorded_at:  # a tie keeps the later-inserted fact
            newest = fact
    if newest.kind in ("add", "observed", "reopened"):
        state = "live"
    elif newest.kind == "gone":
        state = "gone"
    else:
        state = newest.kind
    return FindingLiveness(
        state=state,
        live=state == "live",
        note=newest.note,
        first_observed_at=min((f.recorded_at for f in seen), default=None),
        last_seen_at=max((f.recorded_at for f in seen), default=None),
        observed_count=sum(1 for f in facts if f.kind == "observed"),
        actor=newest.actor,
    )


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


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
        on `ix_findings_scope_source`, indexed unlike `list_across_routines`. The garden
        bucket's own union reads a routine's own findings through `list_for` and a
        scope's `source="review"` findings through this, side by side."""
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
        `"review"`, `None` reads both. Liveness is derived in Python after
        the SQL read, so implementation tops up windows until `limit` matches or
        exhaustion; `cursor` is a prior :attr:`FindingPage.next_cursor`."""
        ...

    def has_delivery_for_proposal(self, proposal_id: str) -> bool:
        """Whether any fact already carries `proposal_id` — delivery-triggered closure's
        own once-only gate, kind-agnostic so a proposal
        delivered before `delivered` existed (its fact stamped `resolved`) still gates,
        independent of any one finding's current state so a later reopen is never
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

    def record_facts(self, entries: Sequence[FactEntry]) -> None:
        """All-or-nothing — pinned by
        `tests/test_finding_store.py::test_record_facts_is_all_or_nothing`."""
        ...


@dataclass(frozen=True)
class FactEntry:
    """One `record_facts` row — the bulk-write shape `FindingExitService`
    builds one of per finding, per verb."""

    finding_id: str
    kind: str
    at: datetime
    note: str | None
    actor: str | None = None
    proposal_id: str | None = None
    superseded_by: str | None = None


class IFindingExitResolver(Protocol):
    """`FindingExitService.deliver`'s own narrowed shape — delivery-triggered closure,
    not an exit itself, so that collaborator depends on a
    Protocol like every other one it takes."""

    def deliver(
        self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None
    ) -> None: ...


class FindingExitService:
    """The human-driven exit verbs, `reopen`, and `deliver`
    — delivery-triggered and provisional, not an exit, until the owning routine's next
    run settles it. Every method takes already-loaded :class:`Finding` objects
    (`bzh:domain-takes-objects`) and refuses a blank or missing note before writing."""

    def __init__(self, *, repo: IWriteFindingRepository, clock: IClock) -> None:
        self._repo = repo
        self._clock = clock

    def resolve(self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None) -> None:
        self._apply(findings, kind="resolved", note=note, actor=actor, proposal_id=proposal_id)

    def deliver(self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None) -> None:
        """Delivery-triggered closure — `resolved`'s provisional sibling:
        the owning routine's next run re-checks a `delivered` finding, settling it to
        `resolved` if it still holds or reviving it to `live` if it does not, rather than
        a delivery alone declaring the ground changed."""
        self._apply(findings, kind="delivered", note=note, actor=actor, proposal_id=proposal_id)

    def confirm_gone(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._apply(findings, kind="gone-confirmed", note=note, actor=actor)

    def wont_fix(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._apply(findings, kind="wont-fix", note=note, actor=actor)

    def not_a_finding(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._apply(findings, kind="not-a-finding", note=note, actor=actor)

    def supersede(self, findings: Sequence[Finding], *, note: str, actor: str, superseded_by: str) -> None:
        self._apply(findings, kind="superseded", note=note, actor=actor, superseded_by=superseded_by)

    def reopen(self, findings: Sequence[Finding], *, note: str, actor: str) -> None:
        self._apply(findings, kind="reopened", note=note, actor=actor)

    def _apply(
        self,
        findings: Sequence[Finding],
        *,
        kind: str,
        note: str,
        actor: str,
        proposal_id: str | None = None,
        superseded_by: str | None = None,
    ) -> None:
        note = note.strip()
        if not note:
            raise FindingNoteRequiredError(kind)
        at = self._clock.now()
        entries = [
            FactEntry(
                finding_id=finding.finding_id,
                kind=kind,
                at=at,
                note=note,
                actor=actor,
                proposal_id=proposal_id,
                superseded_by=superseded_by,
            )
            for finding in findings
        ]
        self._repo.record_facts(entries)


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
