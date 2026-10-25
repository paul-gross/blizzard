"""``land-clean`` — merge every repository's open change once each of them reads mergeable.

Every pending repository is read before any merges, so one conflicting repository lands nothing; each merge hands the
hub its landing, and waits for it to be durable, before the next merge begins."""

# exemplar: bzh:hub-node-action-shape, bzh:hub-action-purity, bzh:hub-node-outcome-protocol,
# bzh:hub-node-step-idempotence — a hub action: one pass over its three inputs, every write through its sink.

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.actions.effects import EffectStatus, IEffectSink, LandingRecord, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.repositories import RepositoryId
from blizzard.hub.domain.graph.actions import ActionSpec
from blizzard.hub.forge.landing import (
    ChangeListing,
    ForgeError,
    IForgeChanges,
    Mergeability,
    MergeMethod,
    VisitForges,
)

# exemplar: the catalogue entry — every answer is declared, so a graph's judgement is checked against it at mint.
LAND_CLEAN = ActionSpec(
    name="land-clean",
    args=frozenset(),
    required_args=frozenset(),
    answers=frozenset({"landed", "conflict", "failure", "pending"}),
    answers_from_arg=None,
    lands=True,
)

_LANDED = "landed"
_CONFLICT = "conflict"
_FAILURE = "failure"
_PENDING = "pending"
_MARKER_PREFIX = "merged/"


@domain_model
@dataclass(frozen=True)
class _Ready:
    """One repository's change the read stage cleared, and the head it merges at."""

    record: str
    repo: str
    changes: IForgeChanges
    number: int
    head_revision: str


class _Unready(Exception):
    """A repository that cannot merge this pass; ``choice`` is what the pass answers for it."""

    def __init__(self, choice: str, reason: str) -> None:
        super().__init__(reason)
        self.choice = choice


class LandClean:
    spec = LAND_CLEAN

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        try:
            ready = await _read_every_repository(visit, forges)
        except _Unready as exc:
            return Pass(choice=exc.choice, log=(str(exc),))
        for item in ready:
            try:
                revision = await item.changes.merge(
                    item.number, item.head_revision, MergeMethod.MERGE, f"Merge #{item.number}"
                )
            except ForgeError as exc:
                # exemplar: a merge that did not land is re-read on the next pass, never retried within this one.
                return Pass(choice=_PENDING, log=(f"merge of {item.repo}#{item.number} did not land: {exc}",))
            landing = LandingRecord(repository=item.record, repo=item.repo, revision=revision)
            # exemplar: the landing is durable before the next merge, so a re-run skips exactly what landed.
            if (await effects.record(landing)).status is EffectStatus.FENCED:
                return Pass(choice=_FAILURE, log=(f"{item.repo} landed, but the chunk has moved past this visit",))
        return Pass(choice=_LANDED)


async def _read_every_repository(visit: Visit, forges: VisitForges) -> list[_Ready]:
    """Each repository still to land and its mergeable change — raising :class:`_Unready` at the first that is
    not, before any merge. A repository whose ``merged/<repo>`` marker the visit holds is skipped."""
    landed = visit.marker_names()
    ready: list[_Ready] = []
    for index, row in enumerate(visit.git_commits.rows):
        repo, target = visit.resolution.qualified[index], visit.resolution.targets[index]
        if f"{_MARKER_PREFIX}{repo}" in landed:
            continue
        changes, branch = forges[RepositoryId(target.record)].changes, row.data.partition(":")[0]
        listed = await changes.changes(ChangeListing.OPEN, target.base_branch, branch)
        if not listed:
            raise _Unready(_CONFLICT, f"{repo}:{branch} has no open change")
        live = await changes.change(listed[0].number)
        if live.mergeability is Mergeability.PENDING:
            raise _Unready(_PENDING, f"{repo}#{live.number} has no mergeability yet")
        if live.mergeability is not Mergeability.MERGEABLE:
            raise _Unready(_CONFLICT, f"{repo}#{live.number} is {live.mergeability}")
        ready.append(_Ready(target.record, repo, changes, live.number, live.head_revision))
    return ready
