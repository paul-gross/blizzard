"""A change a person, rather than the hub, decides whether to merge."""

import json
from datetime import datetime, timedelta
from typing import Protocol

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.landing.merge_on_checks import pr_body, pr_title
from blizzard.hub.delivery.actions.landing.repo import (
    AwaitingMerge,
    Held,
    Landed,
    NeedsPerson,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.landing_policies import DEFAULT_PARK_TIMEOUT_HOURS
from blizzard.hub.forge.landing import Change, ChangeListing, ChangeState, ForgeNotFound, ForgeRejected

_OPEN = crashpoint("policy.after-open-change.before-marker", "a parked change is open without its marker")
_MERGED = crashpoint("policy.after-merged-read.before-marker", "a parked change is merged without its landing record")
_LIMIT = 100


class ParkMarker(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def epoch(self) -> int: ...

    @property
    def produced_at(self) -> datetime | None: ...


def latest_marker(repo: RepoLanding) -> ParkMarker | None:
    prefix = f"delivery-pr/{repo.repo}/"
    return next((row for row in reversed(repo.run.visit.markers) if row.name.startswith(prefix)), None)


class Park:
    async def step(self, repo: RepoLanding, *, watch: bool = False) -> RepoState:
        visit = repo.run.visit
        marker = latest_marker(repo)
        change: Change | None = None
        if marker is not None:
            number = int(marker.name.rsplit("/", 1)[1])
            try:
                closed = await repo.ask(repo.forge.changes.recently_closed(repo.base_branch, _LIMIT))
                change = next((item for item in closed if item.number == number), None)
                if change is None and len(closed) == _LIMIT:
                    change = await repo.ask(repo.forge.changes.change(number))
                if change is None:
                    change = await repo.ask(repo.forge.changes.change(number)) if marker.epoch != visit.epoch else None
            except (ForgeRejected, ForgeNotFound):
                return self._waiting(repo, number, marker)
            if change is None:
                if not any(row.name == "awaiting-external-merge" and row.epoch == visit.epoch for row in visit.markers):
                    await repo.run.post("awaiting-external-merge", "")
                return self._waiting(repo, number, marker)
            if change.state is ChangeState.CLOSED and (
                (change.closed_at is not None and change.closed_at < visit.first_turn_at)
                or (change.closed_at is None and marker.epoch != visit.epoch)
            ):
                if watch:
                    return Held()
                change = None
                marker = None
        if change is None and marker is None:
            try:
                closed = await repo.ask(repo.forge.changes.changes(ChangeListing.CLOSED, repo.base_branch, repo.branch))
                change = next(
                    (
                        item
                        for item in closed
                        if item.state is ChangeState.MERGED
                        and item.closed_at is not None
                        and item.closed_at >= visit.first_turn_at
                    ),
                    None,
                )
                if change is None:
                    found = await repo.ask(
                        repo.forge.changes.changes(ChangeListing.OPEN, repo.base_branch, repo.branch)
                    )
                    if found:
                        change = found[0]
                    else:
                        change = await repo.ask(
                            repo.forge.changes.open_change(repo.branch, repo.base_branch, pr_title(repo), pr_body(repo))
                        )
                        _OPEN.reached()
            except (ForgeRejected, ForgeNotFound) as exc:
                return Waiting(str(exc))
        assert change is not None
        if change.state is ChangeState.MERGED:
            if change.merged_revision is None:
                return Waiting(f"{repo.repo}#{change.number} merged without a revision")
            if marker is None or marker.epoch != visit.epoch:
                await repo.run.post(
                    f"delivery-pr/{repo.repo}/{change.number}",
                    json.dumps({"repo": repo.repo, "number": change.number, "url": change.web_url}),
                )
            if not any(row.name == "awaiting-external-merge" and row.epoch == visit.epoch for row in visit.markers):
                await repo.run.post("awaiting-external-merge", "")
            if change.head_revision != repo.commit:
                comparison = (
                    await repo.compare(repo.commit, change.head_revision)
                    if Capability.COMPARE in repo.forge.offered
                    else None
                )
                revisions = [c.revision for c in comparison.commits] if comparison else [change.head_revision]
                await repo.run.post(
                    "delivery-findings", json.dumps({"repo": repo.repo, "person_added_commits": revisions})
                )
            _MERGED.reached()
            return Landed(change.merged_revision)
        if change.state is ChangeState.CLOSED:
            return NeedsPerson(
                ActionEscalation(EscalationCause.CHANGE_CLOSED, f"{repo.repo}#{change.number} closed without merging")
            )
        name = f"delivery-pr/{repo.repo}/{change.number}"
        if marker is None or marker.epoch != visit.epoch:
            await repo.run.post(name, json.dumps({"repo": repo.repo, "number": change.number, "url": change.web_url}))
        if not any(row.name == "awaiting-external-merge" and row.epoch == visit.epoch for row in visit.markers):
            await repo.run.post("awaiting-external-merge", "")
        return self._waiting(repo, change.number, marker)

    def _waiting(self, repo: RepoLanding, number: int, marker: ParkMarker | None) -> RepoState:
        visit = repo.run.visit
        if marker is not None and marker.epoch == visit.epoch and marker.produced_at is not None:
            hours = repo.record.park_timeout_hours or DEFAULT_PARK_TIMEOUT_HOURS
            if visit.now - marker.produced_at >= timedelta(hours=hours):
                return NeedsPerson(
                    ActionEscalation(
                        EscalationCause.PARK_TIMEOUT, f"{repo.repo}#{number} exceeded its {hours} hour review timeout"
                    )
                )
        return AwaitingMerge(number)
