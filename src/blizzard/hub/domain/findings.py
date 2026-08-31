"""Finding domain model — a durable observation a routine's run recorded (blizzard#390).

The no-stored-column contract is `src/blizzard/hub/store/schema.py`'s own (D2, D4); only
a ``gone`` fact takes a finding out of the live bucket, reversibly (D3). ``class_``/
``locus`` are opaque to the hub
(blizzard-context:/domain/findings-and-proposals.md §`class` and `locus` are opaque)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

FACT_KINDS = frozenset({"add", "observed", "gone"})


class UnknownFactKindError(ValueError):
    """A `finding_facts` row named a `kind` outside `FACT_KINDS` (D2) — a typo here would
    otherwise silently fail every `!= "gone"` liveness check."""

    def __init__(self, kind: str) -> None:
        super().__init__(f"unknown finding-fact kind {kind!r}")


@dataclass(frozen=True)
class Finding:
    finding_id: str
    routine_name: str  # D5 — a routine's own name, not its surrogate id
    scope_slug: str
    class_: str
    locus: str
    summary: str
    introduced: str | None
    #: schema.py's `findings` table carries no such column (D2-D4).
    live: bool
    last_seen_at: datetime | None
    observed_count: int


@dataclass(frozen=True)
class FindingFact:
    """One `add`/`observed`/`gone` transformation (D2) — append-only, oldest first."""

    kind: str  # add | observed | gone
    recorded_at: datetime
    note: str | None = None


@dataclass(frozen=True)
class FindingLiveness:
    """The newest-fact-wins read over a finding's facts (D2-D4) — never persisted."""

    live: bool
    last_seen_at: datetime | None
    observed_count: int


def derive_liveness(facts: Sequence[FindingFact]) -> FindingLiveness:
    """`gone` takes a finding out of the live bucket only until a later fact restores it
    (D3); `last_seen_at` is the newest non-`gone` fact's instant by `recorded_at` — not
    insertion order, so out-of-order ingestion still derives correctly — and
    `observed_count` counts only `observed` facts — re-confirmations, not the initial
    `add`."""
    if not facts:
        return FindingLiveness(live=True, last_seen_at=None, observed_count=0)
    seen = [f for f in facts if f.kind != "gone"]
    newest = facts[0]
    for fact in facts[1:]:
        if fact.recorded_at >= newest.recorded_at:  # a tie keeps the later-inserted fact
            newest = fact
    return FindingLiveness(
        live=newest.kind != "gone",
        last_seen_at=max((f.recorded_at for f in seen), default=None),
        observed_count=sum(1 for f in facts if f.kind == "observed"),
    )


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadFindingRepository(Protocol):
    """Read-only finding access. Controllers at the edges depend on this variant."""

    def get(self, finding_id: str) -> Finding | None: ...

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """A routine's findings under one scope
        (blizzard-product:/plans/garden/machinery.md §Managing findings and proposals) —
        live only, unless `include_gone` (D3)."""
        ...

    def list_for_routine(self, routine_name: str, *, include_gone: bool = False) -> list[Finding]:
        """Every finding live on `routine_name`, across every scope it holds (blizzard#393
        Phase 4) — `list_for`'s scope-narrowed sibling, minus the `scope_slug` filter.
        Live only, unless `include_gone` (D3)."""
        ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name`
        (blizzard-product:/plans/garden/machinery.md §What the store buys) — a count,
        never the rows themselves."""
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

    def record_fact(self, finding_id: str, *, kind: str, at: datetime, note: str | None = None) -> None:
        """Append an `observed`/`gone` fact (D2) — never touches the `findings` row."""
        ...


@dataclass(frozen=True)
class FindingSet:
    """The set a delivered finding list mints, one per artifact (D6) — scope, the
    per-repository revisions, and the routine's measurement live here, never per finding."""

    finding_set_id: str
    artifact_id: str
    chunk_id: str
    scope_slug: str
    revisions: dict[str, str]
    measurement: str | None


class IReadFindingSetRepository(Protocol):
    """Read-only finding-set access. Controllers at the edges depend on this variant."""

    def get(self, finding_set_id: str) -> FindingSet | None: ...

    def list_for_chunk(self, chunk_id: str) -> list[FindingSet]:
        """A run's own delivered sets (D6) — filtered on `ix_finding_sets_chunk_id`."""
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
        revisions: dict[str, str],
        measurement: str | None,
    ) -> FindingSet:
        """Insert the set row — one per artifact (D6, its own `uq`-backed unique FK)."""
        ...
