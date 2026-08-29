"""Finding domain model — a durable observation a routine's run recorded (blizzard#390).

Liveness, last-seen, and the observed count are derived from ``finding_facts``, never a
column (D2, D4): the newest fact wins, and only a ``gone`` fact takes a finding out of
the live bucket, reversibly (D3). ``class_``/``locus`` are opaque to the hub
(machinery.md §Findings are artifacts)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class Finding:
    finding_id: str
    routine_name: str  # D5 — a routine's own name, not its surrogate id
    scope_slug: str
    class_: str
    locus: str
    summary: str
    introduced: str | None
    #: Derived from `finding_facts` at read time (D2-D4) — never a stored column.
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
    (D3); `last_seen_at` is the newest non-`gone` fact's instant, and `observed_count`
    counts only `observed` facts — re-confirmations, not the initial `add`."""
    if not facts:
        return FindingLiveness(live=True, last_seen_at=None, observed_count=0)
    seen = [f for f in facts if f.kind != "gone"]
    return FindingLiveness(
        live=facts[-1].kind != "gone",
        last_seen_at=seen[-1].recorded_at if seen else None,
        observed_count=sum(1 for f in facts if f.kind == "observed"),
    )


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadFindingRepository(Protocol):
    """Read-only finding access. Controllers at the edges depend on this variant."""

    def get(self, finding_id: str) -> Finding | None: ...

    def list_for(self, routine_name: str, scope_slug: str, *, include_gone: bool = False) -> list[Finding]:
        """A routine's findings under one scope (machinery.md §Managing findings and
        proposals) — live only, unless `include_gone` (D3)."""
        ...

    def count_by_class(self, routine_name: str, class_: str) -> int:
        """How often `class_` recurs for `routine_name` (§What the store buys) — a
        count, never the rows themselves."""
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
