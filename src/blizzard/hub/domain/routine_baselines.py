"""Routine baselines — a read-only composition over the finding-set and delivery seams
(blizzard#399, D5): one entry per scope a routine has swept, each carrying the baseline
finding set's id, its recorded instant, and per repo the recorded revision with how
much has landed since (D1).

The instant is decoded from the finding-set id, never stored (D2) — the same idiom
`foundation/ids.py`'s own docstring names for an entity that stores no timestamp
column. Absence from the returned list *is* "never swept" (D5); this composes no
opinion of its own about the scopes a routine has never touched."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.ids import Id
from blizzard.hub.domain.chunks.delivery import IReadChunkDeliveryRepository
from blizzard.hub.domain.findings import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.routines import Routine


@dataclass(frozen=True)
class RepoLandings:
    """One repo's baseline revision and how much has landed against it since —
    `IReadChunkDeliveryRepository.count_landed_since`'s own fact (D1)."""

    repo: str
    revision: str
    landed_since: int


@dataclass(frozen=True)
class RoutineBaseline:
    """One (routine, scope) pair's newest finding set (D5)."""

    scope_slug: str
    finding_set_id: str
    recorded_at: datetime
    repos: list[RepoLandings]


class MalformedFindingSetIdError(ValueError):
    """A `finding_sets` row carried an id outside the prefixed-ULID shape
    `foundation/ids.py` mints — every id this service reads was minted by the hub
    itself, so this names a store-level invariant break, not a user-facing refusal."""

    def __init__(self, finding_set_id: str) -> None:
        super().__init__(f"finding-set id {finding_set_id!r} does not decode a mint instant")


class RoutineBaselineService:
    """The read-only baseline composition D5 names."""

    def __init__(self, *, finding_sets: IReadFindingSetRepository, delivery: IReadChunkDeliveryRepository) -> None:
        self._finding_sets = finding_sets
        self._delivery = delivery

    def baselines_for(self, routine: Routine) -> list[RoutineBaseline]:
        """Newest-swept-first (`finding_set_id` descending) — the picker's own ordering
        cue (D5). Takes the already-resolved routine (`bzh:domain-takes-objects`), the
        same shape `RunService.run` takes: the caller resolves `routine_id` to its
        `Routine` first."""
        sets = self._finding_sets.newest_by_scope_for_routine(routine.name)
        baselines = [self._baseline_of(finding_set) for finding_set in sets]
        return sorted(baselines, key=lambda b: b.finding_set_id, reverse=True)

    def _baseline_of(self, finding_set: FindingSet) -> RoutineBaseline:
        minted_id = Id.parse(finding_set.finding_set_id)
        recorded_at = minted_id.minted_at if minted_id is not None else None
        if recorded_at is None:
            raise MalformedFindingSetIdError(finding_set.finding_set_id)
        repos = [
            RepoLandings(
                repo=repo, revision=revision, landed_since=self._delivery.count_landed_since(repo, recorded_at)
            )
            for repo, revision in sorted(finding_set.revisions.items())
        ]
        return RoutineBaseline(
            scope_slug=finding_set.scope_slug,
            finding_set_id=finding_set.finding_set_id,
            recorded_at=recorded_at,
            repos=repos,
        )
