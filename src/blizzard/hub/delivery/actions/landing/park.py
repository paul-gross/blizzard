"""A change a person, rather than the hub, decides whether to merge."""

import json
from datetime import timedelta

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.landing.merge_on_checks import pr_body, pr_title
from blizzard.hub.delivery.actions.landing.no_code_proof import proves_no_code, record_no_code
from blizzard.hub.delivery.actions.landing.repo import (
    AwaitingMerge,
    ForgeEscalation,
    Held,
    Landed,
    NeedsPerson,
    NotRecorded,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.actions.pr_markers import (
    PrMarker,
    latest_pr_marker,
    pr_marker_content,
    pr_marker_name,
    pr_marker_number,
)
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.landing_policies import DEFAULT_PARK_TIMEOUT_HOURS
from blizzard.hub.forge.landing import (
    Change,
    ChangeListing,
    ChangeState,
    ForgeNotFound,
    ForgeRejected,
    ForgeUnavailable,
)

_OPEN = crashpoint("policy.after-open-change.before-marker", "a parked change is open without its marker")
_MERGED = crashpoint("policy.after-merged-read.before-marker", "a parked change is merged without its landing record")
_LIMIT = 100


class Park:
    async def step(self, repo: RepoLanding, *, watch: bool = False) -> RepoState:
        visit = repo.run.visit
        marker = latest_pr_marker(visit.markers, repo.repo)
        change: Change | None = None
        if marker is not None:
            number = pr_marker_number(marker)
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
                if change is None and self._proof_offered(repo) and await proves_no_code(repo):
                    return await record_no_code(repo)
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
            if change.base != repo.base_branch:
                return self._waiting(repo, change.number, marker)
            if change.merged_revision is None:
                return Waiting(f"{repo.repo}#{change.number} merged without a revision")
            _MERGED.reached()
            # The forge's merge controls are the authority: the merged revision lands whatever the final head.
            return Landed(change.merged_revision, lambda: self._annotate(repo, change, marker))
        if change.state is ChangeState.CLOSED:
            return NeedsPerson(
                ActionEscalation(EscalationCause.CHANGE_CLOSED, f"{repo.repo}#{change.number} closed without merging")
            )
        if marker is None or marker.epoch != visit.epoch:
            await repo.run.post(
                pr_marker_name(repo.repo, change.number),
                pr_marker_content(repo.repo, change.number, change.web_url),
            )
        if not any(row.name == "awaiting-external-merge" and row.epoch == visit.epoch for row in visit.markers):
            await repo.run.post("awaiting-external-merge", "")
        return self._waiting(repo, change.number, marker)

    @staticmethod
    def _proof_offered(repo: RepoLanding) -> bool:
        return {Capability.REFS_READ, Capability.COMPARE} <= repo.forge.offered

    async def _annotate(self, repo: RepoLanding, change: Change, marker: PrMarker | None) -> None:
        """The merged change's reference and changed-head evidence, each written best-effort after the landing."""
        visit = repo.run.visit
        steps: list[tuple[str, str]] = []
        if marker is None or marker.epoch != visit.epoch:
            steps.append(
                (
                    pr_marker_name(repo.repo, change.number),
                    pr_marker_content(repo.repo, change.number, change.web_url),
                )
            )
        if not any(row.name == "awaiting-external-merge" and row.epoch == visit.epoch for row in visit.markers):
            steps.append(("awaiting-external-merge", ""))
        if change.head_revision != repo.commit:
            evidence: dict[str, object] = {
                "repo": repo.repo,
                "submitted": repo.commit,
                "merged_head": change.head_revision,
            }
            if Capability.COMPARE in repo.forge.offered:
                try:
                    comparison = await repo.compare(repo.commit, change.head_revision)
                except (ForgeEscalation, ForgeUnavailable):
                    comparison = None
                if comparison is not None:
                    evidence["compared_commits"] = [c.revision for c in comparison.commits]
            steps.append(("delivery-findings", json.dumps(evidence)))
        for name, content in steps:
            try:
                await repo.run.post(name, content)
            except NotRecorded as exc:
                repo.run.say(f"{repo.repo}: {name} was not written (non-fatal): {exc}")

    def _waiting(self, repo: RepoLanding, number: int, marker: PrMarker | None) -> RepoState:
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
