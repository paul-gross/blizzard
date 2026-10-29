"""``merge-on-checks`` — lands a repository's commit on its base branch through a change, self-healing: ``behind``
updates the change from its base, ``conflicting`` is a conflict, a head beyond the submitted commit and its base merges
is a failure, and anything else unsettled waits. Proven, it is ready, and its landing is the change's merge with a merge
commit at the head its checks passed on."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Any

from blizzard.hub.delivery.actions.landing.checks import (
    BOUNCE,
    CI_WATCH_STATES,
    FAILED,
    FOREIGN,
    INHERITED_FAILURE,
    UPDATE,
    WAIT,
    Route,
    inherited,
    read_verdict,
    rerun_marker,
    rerun_once,
)
from blizzard.hub.delivery.actions.landing.head_gate import gate_head
from blizzard.hub.delivery.actions.landing.repo import (
    Admitted,
    Conflict,
    Failure,
    ForgeEscalation,
    Inherited,
    Landed,
    LandedRevisionUnknown,
    NeedsPerson,
    Ready,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.forge.landing import (
    Change,
    ChangeListing,
    ChangeState,
    Check,
    ForgeNotFound,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    Update,
    UpdateOutcome,
)

_PR_MARKER_PREFIX = "delivery-pr/"

# GitHub's PR title limit; longer titles get an ellipsis.
_PR_TITLE_MAX = 256


class PullRequestOpenError(Exception):
    """The forge refused to open the chunk's PR — whether that is a conflict or merely worth another pass is
    the stage's call."""


class PullRequestLookupError(Exception):
    """A read the merged-PR lookup needs failed outright rather than confirming an answer either way. Never
    degrades to "no merged PR here": that reading reopens the exact duplicate the lookup exists to prevent."""


class NothingToLand(Exception):
    """No-op landing: the base already holds the branch's commits. :attr:`landed_sha` is the base's live
    tip, not the submitted commit."""

    def __init__(self, message: str, landed_sha: str) -> None:
        super().__init__(message)
        self.landed_sha = landed_sha


class MergeDidNotLand(Exception):
    """A merge did not land and a re-read confirms the change is still open; the message is the refusal."""


def _pr_title(repo: RepoLanding) -> str:
    """The visit's feature title, or the branch when none resolved, truncated to :data:`_PR_TITLE_MAX`."""
    title = repo.run.visit.feature_title or repo.branch
    if len(title) > _PR_TITLE_MAX:
        title = title[: _PR_TITLE_MAX - 1].rstrip() + "…"
    return title


def _pr_body(repo: RepoLanding) -> str:
    """A newly opened PR's body: the work items, the chunk id, and its board link."""
    visit = repo.run.visit
    parts = ["Delivered by blizzard."]
    lines = repo.run.item_lines()
    if lines:
        parts.append("Work items:\n" + "\n".join(f"- {line}" for line in lines))
    parts.append(f"Chunk: {visit.chunk.chunk_id}")
    if visit.public_url:
        parts.append(f"Board: {visit.public_url.rstrip('/')}/board/chunk/{visit.chunk.chunk_id}")
    return "\n\n".join(parts) + "\n"


def _merge_message(repo: RepoLanding) -> str:
    """The merge commit message: the title, then each work item's line."""
    title = repo.run.visit.feature_title or f"blizzard: land {repo.repo}"
    lines = repo.run.item_lines()
    return f"{title}\n\n" + "\n".join(lines) if lines else title


@dataclass(frozen=True)
class PullRequest:
    """One repository's change for the chunk's branch, and the sole owner of the forge's change calls.
    :attr:`change` is the change as of the last read."""

    target: RepoLanding
    change: Change

    @classmethod
    async def of(cls, target: RepoLanding) -> PullRequest:
        """The change for the target's branch: an already-merged one first, so a re-visit recovers a merge
        even when its record was interrupted — then the open one, opening one first when neither exists, then
        read live. Raises :class:`PullRequestOpenError` or :class:`PullRequestLookupError` on a hiccup."""
        merged = await cls._merged_for_branch(target)
        if merged is not None:
            return await cls._record(await cls(target, merged).reread())
        existing = await cls._open_for_branch(target)
        if existing is None:
            try:
                existing = await target.ask(
                    target.forge.changes.open_change(
                        target.branch, target.base_branch, _pr_title(target), _pr_body(target)
                    )
                )
            except (ForgeRejected, ForgeNotFound) as exc:
                existing = await cls._after_refused_open(target, exc)
        return await cls._record(await cls(target, existing).reread())

    @classmethod
    async def _after_refused_open(cls, target: RepoLanding, refusal: Exception) -> Change:
        """What a refused open leaves: nothing to land when the base holds the branch, else an open change
        someone else opened meanwhile, else :class:`PullRequestOpenError`."""
        if await target.base_holds(target.branch):
            raise NothingToLand(
                f"{target.repo}:{target.branch} adds no commit {target.base_branch} does not already have",
                await target.base_tip(),
            )
        adopted = await cls._open_for_branch(target)
        if adopted is None:
            raise PullRequestOpenError(f"could not open a PR for {target.repo}:{target.branch}: {refusal}")
        return adopted

    @classmethod
    async def _record(cls, pull: PullRequest) -> PullRequest:
        """Persist the PR identity before a pass can wait on, reject or merge it — one marker per PR."""
        await pull.target.run.post(
            f"{_PR_MARKER_PREFIX}{pull.target.repo}/{pull.number}",
            json.dumps({"repo": pull.target.repo, "number": pull.number, "url": pull.url}),
        )
        return pull

    @staticmethod
    async def _listed(target: RepoLanding, listing: ChangeListing) -> tuple[Change, ...]:
        try:
            return await target.ask(target.forge.changes.changes(listing, target.base_branch, target.branch))
        except ForgeRejected as exc:
            raise PullRequestLookupError(f"could not read {target.repo}'s {listing} changes: {exc}") from exc

    @classmethod
    async def _open_for_branch(cls, target: RepoLanding) -> Change | None:
        listed = await cls._listed(target, ChangeListing.OPEN)
        return listed[0] if listed else None

    @classmethod
    async def _merged_for_branch(cls, target: RepoLanding) -> Change | None:
        """The already-merged change that landed the submitted commit — else the branch's live tip — into the
        target's own base, or ``None`` when the read confirms there is none: never a same-name change merged
        into a different base, and never a refused read, which raises :class:`PullRequestLookupError`."""
        candidates = [c for c in await cls._listed(target, ChangeListing.CLOSED) if c.state is ChangeState.MERGED]
        if not candidates:
            return None
        recorded = next((c for c in candidates if c.head_revision == target.commit), None)
        if recorded is not None:
            return recorded
        try:
            live = await target.ask(target.forge.refs.branch_tip(target.branch))
        except ForgeRejected as exc:
            raise PullRequestLookupError(f"could not read {target.repo}:{target.branch}'s live tip: {exc}") from exc
        if live is None:
            return None  # the forge deleted the head branch on merge: the ref is gone, not unreadable
        return next((c for c in candidates if c.head_revision == live), None)

    def __str__(self) -> str:
        return f"{self.target.repo}#{self.number}"

    @property
    def number(self) -> int:
        return self.change.number

    @property
    def merged(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.change.state is ChangeState.MERGED

    @property
    def mergeability(self) -> Mergeability:
        return self.change.mergeability

    @property
    def head_sha(self) -> str:
        return self.change.head_revision

    @property
    def url(self) -> str:
        return self.change.web_url

    def record(self, decision: str, **fields: Any) -> dict[str, Any]:
        """A findings record for this PR."""
        return {"repo": self.target.repo, "number": self.number, "url": self.url, "decision": decision, **fields}

    async def reread(self) -> PullRequest:
        """The change read live; a read the forge refused raises :class:`PullRequestLookupError`."""
        try:
            return replace(self, change=await self.target.ask(self.target.forge.changes.change(self.number)))
        except (ForgeRejected, ForgeNotFound) as exc:
            raise PullRequestLookupError(f"could not read {self}: {exc}") from exc

    async def update_from_base(self, expected_head: str) -> Update | None:
        """Ask the forge to bring the change up to its base — guarded on ``expected_head``, so updates never
        stack; ``None`` when the forge no longer knows the change."""
        try:
            return await self.target.ask(self.target.forge.changes.update_from_base(self.number, expected_head))
        except ForgeNotFound:
            return None

    async def merge(self, revision: str) -> str:
        """Merge at ``revision`` with a merge commit and return the landed commit. A refused merge re-reads the
        change: one already merged is not remerged, one still open raises :class:`MergeDidNotLand`, and one
        merged at an unknown revision :class:`LandedRevisionUnknown`."""
        try:
            return await self.target.ask(
                self.target.forge.changes.merge(self.number, revision, MergeMethod.MERGE, _merge_message(self.target))
            )
        except (ForgeRejected, ForgeNotFound) as exc:
            refusal = str(exc)
        landed = await self.reread()
        if not landed.merged:
            raise MergeDidNotLand(refusal)
        if landed.change.merged_revision is None:
            raise LandedRevisionUnknown(f"{self} merged but no read carries its landed commit")
        return landed.change.merged_revision


class MergeOnChecks:
    """The ``merge-on-checks`` policy: one repository's change found or opened, its head gated, brought up to its
    base when behind, and its checks read; ready once they are green."""

    async def step(self, repo: RepoLanding) -> RepoState | Admitted:
        try:
            return await self._gated(repo)
        except ForgeEscalation as exc:
            return NeedsPerson(exc.escalation)

    async def _gated(self, repo: RepoLanding) -> RepoState | Admitted:
        """The change found or opened and its head gated; an admitted head's checks wait for every other head."""
        run = repo.run
        try:
            pull = await PullRequest.of(repo)
        except NothingToLand as exc:
            # Nothing to merge, and no poll changes that: the landing stops this repo being pending.
            run.say(f"{exc} — nothing to land")
            return Landed(exc.landed_sha)
        except (LandedRevisionUnknown, PullRequestOpenError, PullRequestLookupError) as exc:
            # A degraded read or a create hiccup is worth another poll — never "not merged".
            run.say(str(exc))
            return Waiting(str(exc))
        except ForgeUnavailable as exc:
            run.hold(exc)
            run.say(f"{repo.repo}: the forge did not answer: {exc}; re-polling")
            return Waiting(str(exc))
        # A merged PR is gated too: one recovered by the live-tip fallback may carry foreign content.
        try:
            gate = await gate_head(repo, pull.head_sha)
        except ForgeUnavailable as exc:
            run.hold(exc)
            run.say(f"{pull}: the forge did not answer: {exc}; re-polling")
            return Waiting(str(exc))
        if not gate.admitted():
            run.say(f"{pull}'s head {pull.head_sha} is not the submitted commit plus base merges")
            foreign = pull.record(FOREIGN, submitted=repo.commit, head=pull.head_sha, offenders=gate.offenders)
            return Failure((foreign,), foreign_head=True)
        return Admitted(lambda: _checked(pull))


async def _checked(pull: PullRequest) -> RepoState:
    """Where a gated change stands on its mergeability and its checks."""
    try:
        return await _routed(pull)
    except ForgeEscalation as exc:
        return NeedsPerson(exc.escalation)


async def _routed(pull: PullRequest) -> RepoState:
    repo, run = pull.target, pull.target.run
    head_sha = pull.head_sha
    state = pull.mergeability
    # A verdict is only worth reading for the states merge eligibility turns on — read once.
    verdict = await read_verdict(repo, head_sha) if state in CI_WATCH_STATES else None
    decision = Route(state, merged=pull.merged, verdict=verdict).decision
    if decision == BOUNCE:
        return Conflict(f"{pull} is dirty (a real merge conflict)")
    if decision == UPDATE:
        # Any refusal other than a named conflict waits — the next poll's state is authoritative.
        try:
            update = await pull.update_from_base(head_sha)
        except ForgeUnavailable as exc:
            run.hold(exc)
            run.say(f"{pull} behind — the forge did not answer its update: {exc}; re-polling")
            return Waiting(str(exc))
        if update is not None and update.outcome is UpdateOutcome.CONFLICT:
            return Conflict(f"{pull} could not be updated from its base without a conflict")
        answered = update.outcome if update is not None else "the change was not found"
        run.say(f"{pull} behind — update from its base requested ({answered}); re-polling")
        return Waiting(f"{pull} behind")
    if decision == WAIT:
        findings: dict[str, Any] | None = None
        if verdict is not None:
            # A degraded CI read falls through to the plain wait below.
            if verdict.decision == FAILED:
                return await _failed(
                    pull, verdict.failing, verdict.failure_rows(await read_verdict(repo, repo.base_branch))
                )
            if verdict.substantive:
                findings = pull.record(WAIT, checks=verdict.running_rows())
        run.say(f"{pull} is {state} — not cleanly mergeable yet; re-polling")
        return Waiting(f"{pull} is {state}", findings)
    # Already merged, or a green verdict at a mergeable or blocked head — eligible.
    return Ready(_merge_at(pull, head_sha))


async def _failed(pull: PullRequest, failing: list[Check], checks: list[dict[str, Any]]) -> RepoState:
    """A change whose checks failed: its own failure, or — every failing check inherited from the base — one re-run
    each before it is charged as inherited."""
    repo, run, head_sha = pull.target, pull.target.run, pull.head_sha
    if not all(inherited(check["base_red"]) for check in checks):
        # Any owned failure charges this chunk; inherited ones on the same repo accompany it in one diagnosis.
        run.say(f"{pull} has a terminal CI check failure — will not re-poll")
        return Failure((pull.record(FAILED, checks=checks),))
    # Every failing check is inherited from the base: re-run each once (a forge that cannot re-run counts as
    # re-run) before charging a repair.
    rerunnable = Capability.CHECKS_RERUN in repo.forge.offered
    already = run.visit.marker_names()
    unsigned = [
        check for check in failing if rerunnable and rerun_marker(repo.repo, check.name, head_sha) not in already
    ]
    if unsigned:
        accepted = [await rerun_once(repo, check, head_sha) for check in unsigned]
        if False in accepted:
            # The forge will not re-run a check for this credential: no re-run is coming, so the failure routes
            # as an owned one would.
            run.say(f"{pull}'s failing checks could not be re-run — will not re-poll")
            return Failure((pull.record(FAILED, checks=checks),))
        run.say(f"{pull}'s failing checks are inherited from the base — re-requested once; re-polling")
        return Waiting(f"{pull}'s inherited failures re-run")
    record = pull.record(
        INHERITED_FAILURE,
        base_branch=repo.base_branch,
        checks=[
            {
                "name": check.name,
                "conclusion": check.detail,
                "details_url": check.details_url,
                "signature": rerun_marker(repo.repo, check.name, head_sha),
            }
            for check in failing
        ],
    )
    run.say(
        f"{pull}'s failing checks are still inherited from the base after a re-run — not this chunk's defect; "
        "routing to repair the base"
    )
    return Inherited((record,))


def _merge_at(pull: PullRequest, head_sha: str) -> Callable[[], Awaitable[Landed | Waiting]]:
    async def land() -> Landed | Waiting:
        """Merge the change's CURRENT head, which a self-heal update may have advanced past the submitted commit."""
        try:
            return Landed(await pull.merge(head_sha))
        except MergeDidNotLand as exc:
            # Not an already-merged earlier pass (`merge` absorbs that) — a race worth re-polling.
            return Waiting(f"merge of {pull} did not land ({exc}); will re-poll")
        except (LandedRevisionUnknown, PullRequestLookupError) as exc:
            # Merged, but no read named its commit — the next poll recovers it as already merged.
            return Waiting(f"{exc}; will re-poll")

    return land
