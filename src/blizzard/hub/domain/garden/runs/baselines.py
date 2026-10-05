"""Routine baselines — a read-only composition over the finding-set and delivery seams:
one entry per scope a routine has swept, each carrying the baseline
finding set's id, its recorded instant (`Id.minted_at`), and per repo how much has
landed since. See `IReadFindingSetRepository.newest_by_scope_for_routine` for what
absence means."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.ids import Id
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.ports.delivery import IReadChunkDeliveryRepository
from blizzard.hub.domain.garden.findings.model import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.garden.routines import Routine


@domain_model
@dataclass(frozen=True)
class RepoLandings:
    """One repo's baseline revision and how much has landed against it since —
    `IReadChunkDeliveryRepository.count_landed_since`'s own fact."""

    repo: str
    revision: str
    landed_since: int


@domain_model
@dataclass(frozen=True)
class RoutineBaseline:
    """One (routine, scope) pair's newest finding set."""

    scope_slug: str
    finding_set_id: str
    recorded_at: datetime
    repos: list[RepoLandings]

    @classmethod
    def of(cls, finding_set: FindingSet, *, recorded_at: datetime, landed_since: Mapping[str, int]) -> RoutineBaseline:
        """``finding_set`` as a baseline, one repo entry per recorded revision in repo
        order, each with what ``landed_since`` counts for it."""
        return cls(
            scope_slug=finding_set.scope_slug,
            finding_set_id=finding_set.finding_set_id,
            recorded_at=recorded_at,
            repos=[
                RepoLandings(repo=repo, revision=revision, landed_since=landed_since[repo])
                for repo, revision in sorted(finding_set.revisions.items())
            ],
        )


class MalformedFindingSetIdError(ValueError):
    """A `finding_sets` row carried an id outside the prefixed-ULID shape
    `foundation/ids.py` mints — every id this service reads was minted by the hub
    itself, so this names a store-level invariant break, not a user-facing refusal."""

    def __init__(self, finding_set_id: str) -> None:
        super().__init__(f"finding-set id {finding_set_id!r} does not decode a mint instant")


def recorded_at_of(finding_set: FindingSet) -> datetime:
    """The instant ``finding_set`` was recorded, decoded from its minted id."""
    minted_id = Id.parse(finding_set.finding_set_id)
    recorded_at = minted_id.minted_at if minted_id is not None else None
    if recorded_at is None:
        raise MalformedFindingSetIdError(finding_set.finding_set_id)
    return recorded_at


def newest_swept_first(baselines: Sequence[RoutineBaseline]) -> list[RoutineBaseline]:
    """Newest-swept first — ``finding_set_id`` descending, the picker's ordering cue."""
    return sorted(baselines, key=lambda b: b.finding_set_id, reverse=True)


class RoutineBaselineService:
    """The read-only baseline composition."""

    def __init__(self, *, finding_sets: IReadFindingSetRepository, delivery: IReadChunkDeliveryRepository) -> None:
        self._finding_sets = finding_sets
        self._delivery = delivery

    def baselines_for(self, routine: Routine) -> list[RoutineBaseline]:
        """Newest-swept-first. Takes the already-resolved routine
        (`bzh:domain-takes-objects`)."""
        sets = self._finding_sets.newest_by_scope_for_routine(routine.name)
        return newest_swept_first([self._baseline_of(finding_set) for finding_set in sets])

    def _baseline_of(self, finding_set: FindingSet) -> RoutineBaseline:
        recorded_at = recorded_at_of(finding_set)
        # Deferred: N here is a routine's own repo count, each with its own `since`, not
        # the fleet's — small and bounded regardless of fleet size.
        landed_since = {
            repo: self._delivery.count_landed_since(repo, recorded_at) for repo in sorted(finding_set.revisions)
        }
        return RoutineBaseline.of(finding_set, recorded_at=recorded_at, landed_since=landed_since)
