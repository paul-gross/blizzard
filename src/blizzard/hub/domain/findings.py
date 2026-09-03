"""Finding domain model — a durable observation a routine's run recorded (blizzard#390).

The no-stored-column contract is `src/blizzard/hub/store/schema.py`'s own (D2, D4).
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
    {"add", "observed", "gone", "resolved", "gone-confirmed", "wont-fix", "not-a-finding", "superseded", "reopened"}
)

#: The human-driven verbs that take a finding out of the live set for good (blizzard#394
#: D2) — `reopened` is deliberately excluded, since it is the fact kind that undoes one.
EXIT_KINDS = frozenset({"resolved", "gone-confirmed", "wont-fix", "not-a-finding", "superseded"})

#: The ground itself changed — work landed, or a person confirmed non-reproduction.
OUTFLOW_KINDS = frozenset({"resolved", "gone-confirmed"})

#: A judgment call about the finding, not the code (blizzard#394 D2).
WITHDRAWN_KINDS = EXIT_KINDS - OUTFLOW_KINDS


class UnknownFactKindError(ValueError):
    """A `finding_facts` row named a `kind` outside `FACT_KINDS` (D2) — refused at the
    write path so `derive_liveness`'s newest-fact-wins fold, which assumes every kind is
    one of the nine, never has to reason about a stray value."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"unknown finding-fact kind {kind!r}")


class FindingNoteRequiredError(ValueError):
    """An exit or `reopened` fact carried a blank or missing note (blizzard#394) — every
    human-driven verb wants one, the way `gone`'s own note already does."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"{kind!r} requires a non-empty note")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    routine_name: str  # D5 — a routine's own name, not its surrogate id
    scope_slug: str
    class_: str
    locus: str
    summary: str
    introduced: str | None
    #: The `introduced` commit's own authored instant — nullable, never backfilled
    #: (blizzard#394 D5): null wherever unresolved, by design.
    introduced_at: datetime | None
    #: When a routine first recorded this finding — the `add` fact's own instant.
    #: Distinct from `introduced_at`: that is when the *commit* landed, this is when
    #: the garden first *saw* it. Derived, never stored.
    first_observed_at: datetime | None
    #: schema.py's `findings` table carries no such column (D2-D4).
    live: bool
    #: "live", "gone", or one of `EXIT_KINDS` — the newest fact's own kind (blizzard#394).
    state: str
    #: The newest fact's own note, whatever kind it is; `None` for a kind that carries
    #: none (blizzard#394).
    note: str | None
    last_seen_at: datetime | None
    observed_count: int


@dataclass(frozen=True)
class FindingFact:
    """One `add`/`observed`/`gone`/exit/`reopened` transformation (D2, blizzard#394) —
    append-only, oldest first."""

    kind: str
    recorded_at: datetime
    note: str | None = None
    #: Who recorded a human-driven fact — `None` for a run-driven `add`/`observed`/`gone`
    #: (blizzard#394).
    actor: str | None = None
    #: The proposal a `resolved` fact answered, when the delivery-triggered drain
    #: recorded it (blizzard#394 Phase 3) — always `None` for a hand resolution.
    proposal_id: str | None = None
    #: The absorbing finding, set only on a `superseded` fact (blizzard#394).
    superseded_by: str | None = None


@dataclass(frozen=True)
class FindingLiveness:
    """The newest-fact-wins read over a finding's facts (D2-D4, blizzard#394) — never
    persisted."""

    state: str
    live: bool
    note: str | None
    first_observed_at: datetime | None
    last_seen_at: datetime | None
    observed_count: int


def derive_liveness(facts: Sequence[FindingFact]) -> FindingLiveness:
    """The newest-fact-wins read over a finding's facts (D1-D3, blizzard#394): any later
    fact reverses `gone`, but only `reopened` reverses an `EXIT_KINDS` verb.
    `first_observed_at`/`last_seen_at` are the min/max of the same `add`/`observed` span
    and use `recorded_at`, not insertion order, so out-of-order ingestion still derives
    correctly."""
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
    )


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadFindingRepository(Protocol):
    """Read-only finding access. Controllers at the edges depend on this variant."""

    def get(self, finding_id: str) -> Finding | None: ...

    def get_many(self, finding_ids: Sequence[str]) -> dict[str, Finding]:
        """`get`'s batched sibling, keyed by `finding_id` — a bulk exit verb's read side
        (blizzard#394), so it costs one query pair, not one pair per row."""
        ...

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """A routine's findings under one scope
        (blizzard-product:/plans/garden/machinery.md §Managing findings and proposals) —
        live only, unless `include_gone` (D3), which also surfaces every exited finding,
        not just a merely `gone` one."""
        ...

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        """Every finding live on `routine_name`, across every scope it holds (blizzard#393
        Phase 4) — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter.
        Live only, unless `include_gone` (D3), which also surfaces every exited finding."""
        ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name`
        (blizzard-product:/plans/garden/machinery.md §What the store buys) — a count,
        never the rows themselves."""
        ...

    def has_resolution_for_proposal(self, proposal_id: str) -> bool:
        """Whether any `resolved` fact already carries `proposal_id` — delivery-triggered
        resolution's own once-only gate (blizzard#394), independent of any one finding's
        current state so a later reopen of a resolved finding is never silently redone."""
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
        """Insert the finding row and its own `add` fact (D2), in one transaction."""
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
        """Append one fact (D2) — never touches the `findings` row."""
        ...

    def record_facts(self, entries: Sequence[FactEntry]) -> None:
        """All-or-nothing (D7) — pinned by
        `tests/test_finding_store.py::test_record_facts_is_all_or_nothing`."""
        ...


@dataclass(frozen=True)
class FactEntry:
    """One `record_facts` row (blizzard#394) — the bulk-write shape `FindingExitService`
    builds one of per finding, per verb."""

    finding_id: str
    kind: str
    at: datetime
    note: str | None
    actor: str | None = None
    proposal_id: str | None = None
    superseded_by: str | None = None


class IFindingExitResolver(Protocol):
    """`FindingExitService.resolve`'s own shape — the one exit verb delivery-triggered
    resolution calls, narrowed so that collaborator depends on a Protocol like every
    other one it takes (blizzard#394)."""

    def resolve(
        self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None
    ) -> None: ...


class FindingExitService:
    """The human-driven exit verbs (blizzard#394) that decide a finding's fate for good,
    plus `reopen`, the way back. Every method takes already-loaded :class:`Finding` objects
    (`bzh:domain-takes-objects`) and refuses a blank or missing note before writing."""

    def __init__(self, *, repo: IWriteFindingRepository, clock: IClock) -> None:
        self._repo = repo
        self._clock = clock

    def resolve(self, findings: Sequence[Finding], *, note: str, actor: str, proposal_id: str | None = None) -> None:
        self._apply(findings, kind="resolved", note=note, actor=actor, proposal_id=proposal_id)

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
    """The set a delivered finding list mints, one per artifact (D6) — scope, the
    per-repository revisions, and the routine's measurement live here, never per finding."""

    finding_set_id: str
    artifact_id: str
    chunk_id: str
    scope_slug: str
    routine_name: str  # D5 — a routine's own name, not its surrogate id (blizzard#392)
    revisions: dict[str, str]
    measurement: str | None


class IReadFindingSetRepository(Protocol):
    """Read-only finding-set access. Controllers at the edges depend on this variant."""

    def get(self, finding_set_id: str) -> FindingSet | None: ...

    def list_for_chunk(self, chunk_id: str) -> list[FindingSet]:
        """A run's own delivered sets (D6) — filtered on `ix_finding_sets_chunk_id`."""
        ...

    def newest_for_routine_scope(self, routine_name: str, scope_slug: str) -> FindingSet | None:
        """A routine run's own delta baseline (blizzard#392) — the newest set for the
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
        """Insert the set row — one per artifact (D6, its own `uq`-backed unique FK)."""
        ...
