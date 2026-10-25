"""``land`` — lands a chunk's commits on each repository's base branch through a change, self-healing:
``behind`` updates the change from its base, ``conflicting`` answers ``conflict``, a head beyond the submitted
commit and its base merges answers ``failure``, and anything else unsettled waits (``pending``). Nothing merges
unless every repository is ready; each merge is handed over as a :class:`LandingRecord` and awaited before the next
repository is touched. A pass skips what its node's markers say earlier passes did, and speaks to each repository
only through its :class:`RepositoryForge`; a forge fault no worker can repair escalates to a person."""

from __future__ import annotations

import json
from collections.abc import Awaitable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any

from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.base_merge import contributes_only_base_change
from blizzard.hub.delivery.actions.land_checks import (
    BOUNCE,
    CI_WATCH_STATES,
    FAILED,
    FOREIGN,
    INHERITED_FAILURE,
    UPDATE,
    WAIT,
    Findings,
    HeadGate,
    Route,
    Verdict,
    first_parent_chain,
    inherited,
)
from blizzard.hub.domain.actions.effects import (
    ActionEscalation,
    EffectStatus,
    IEffectSink,
    LandingRecord,
    Marker,
    Pass,
)
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.domain.graph.actions import LAND
from blizzard.hub.forge.landing import (
    Change,
    ChangeListing,
    ChangeState,
    Check,
    Comparison,
    ComparisonStatus,
    ForgeNotFound,
    ForgeObject,
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    MergeMethod,
    RepositoryForge,
    Update,
    UpdateOutcome,
    VisitForges,
)

# The answers a pass gives.
_LANDED = "landed"
_CONFLICT = "conflict"
_PENDING = "pending"
_FAILURE = "failure"

_MARKER_PREFIX = "merged/"
_PR_MARKER_PREFIX = "delivery-pr/"
# The marker a terminal-CI-failure or a substantive wait writes its findings under.
_FINDINGS_NAME = "delivery-findings"
_FOREIGN_FINDINGS_NAME = f"{_FINDINGS_NAME}/foreign-head"
# One per (repo, check name, head sha) an inherited failure was re-run under, read back on a re-visit.
_RERUN_MARKER_PREFIX = "ci-rerun/"

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


class LandedRevisionUnknown(Exception):
    """A landing is confirmed but the revision it landed at cannot be read — no ``merged/<repo>`` marker may
    be written for it, since a guessed revision is worse than none."""


class MergeDidNotLand(Exception):
    """A merge did not land and a re-read confirms the change is still open; the message is the refusal."""


class _Conflict(Exception):
    """Aborts the check stage as a real conflict — nothing has been merged."""


class _NotRecorded(Exception):
    """A marker the pass needs durable was not recorded: the chunk moved past the visit."""


class _LandingFenced(Exception):
    """A merge's landing record was written past the fence: the chunk moved on while it landed."""


class _Escalation(Exception):
    """A forge answer no worker can repair — a rejected credential, a repository the forge does not know."""

    def __init__(self, escalation: ActionEscalation) -> None:
        super().__init__(escalation.detail)
        self.escalation = escalation


def _rerun_marker(repo: str, name: str, head_sha: str) -> str:
    return f"{_RERUN_MARKER_PREFIX}{repo}/{name}/{head_sha}"


@dataclass
class _Run:
    """One pass's channels out: its sink, and the log lines its ``hub-log.land`` artifact carries."""

    visit: Visit
    effects: IEffectSink
    log: list[str] = field(default_factory=list)
    #: The latest moment a rate-limited forge said to come back, read by a ``pending`` answer.
    resume_at: datetime | None = None

    def say(self, line: str) -> None:
        self.log.append(line)

    def hold(self, exc: ForgeUnavailable) -> None:
        """Note a forge that did not answer; a rate limit postpones this pass's next poll to when it lifts."""
        if isinstance(exc, ForgeRateLimited) and (self.resume_at is None or exc.resume_at > self.resume_at):
            self.resume_at = exc.resume_at

    def answer(self, choice: str) -> Pass:
        return Pass(choice=choice, log=tuple(self.log), next_poll_at=self.resume_at if choice == _PENDING else None)

    def escalate(self, escalation: ActionEscalation) -> Pass:
        return Pass(choice=None, log=tuple(self.log), escalation=escalation)

    async def post(self, name: str, content: str) -> None:
        """Record marker ``name``, or raise :class:`_NotRecorded`."""
        result = await self.effects.record(Marker(name, content))
        if result.status is not EffectStatus.RECORDED:
            raise _NotRecorded(f"marker {name!r} was not recorded: {result.detail}")

    async def landed(self, target: _Target, revision: str) -> None:
        """Hand over ``target``'s merge at ``revision`` and wait for it; raise :class:`_LandingFenced` when it
        was written past the fence."""
        result = await self.effects.record(
            LandingRecord(repository=target.record.record, repo=target.repo, revision=revision)
        )
        if result.status is EffectStatus.FENCED:
            raise _LandingFenced(f"{target.repo} landed at {revision}, but {result.detail}")

    def item_lines(self) -> list[str]:
        """``Refs <reference>`` per work item, else its label — never a closing keyword or title."""
        return [
            f"Refs {item['reference']}" if item.get("reference") else str(item["label"])
            for item in self.visit.work_items
        ]


@dataclass(frozen=True)
class _Target:
    """One commit to land and the repository it lands in: its ``owner/repo``, its branch and submitted
    commit, its resolved record, and its forge's roles."""

    run: _Run
    repo: str
    branch: str
    commit: str
    record: ResolvedRepository
    forge: RepositoryForge

    @property
    def base_branch(self) -> str:
        return self.record.base_branch

    def marker(self) -> str:
        return f"{_MARKER_PREFIX}{self.repo}"

    async def ask[T](self, call: Awaitable[T]) -> T:
        """``call``'s answer; a rejected credential or a repository the forge does not know raises
        :class:`_Escalation`, since no worker can repair either."""
        where = f"{self.repo} on {self.record.forge_api_url}"
        try:
            return await call
        except ForgeRejected as exc:
            if exc.reason != ForgeRejected.AUTH:
                raise
            detail = f"{where}: the forge rejected the repository's credential ({exc})"
            raise _Escalation(ActionEscalation(EscalationCause.FORGE_AUTH, detail)) from exc
        except ForgeNotFound as exc:
            if exc.what is not ForgeObject.REPOSITORY:
                raise
            detail = (
                f"{where}: the forge does not know the repository — it does not exist, or cannot be seen with "
                f"this credential ({exc})"
            )
            raise _Escalation(ActionEscalation(EscalationCause.FORGE_CONFIG, detail)) from exc

    async def compare(self, base: str, head: str) -> Comparison | None:
        """The forge's ``base...head`` comparison; ``None`` when the forge refused it or does not know a
        revision."""
        try:
            return await self.ask(self.forge.refs.compare(base, head))
        except (ForgeRejected, ForgeNotFound):
            return None

    async def base_holds(self, ref: str) -> bool:
        """Whether the base branch already holds every commit on ``ref``; a comparison the forge refused is
        ``False`` — never "there was nothing to land"."""
        comparison = await self.compare(self.base_branch, ref)
        return comparison is not None and comparison.status in {ComparisonStatus.IDENTICAL, ComparisonStatus.BEHIND}

    async def base_tip(self) -> str:
        """The base branch's live tip; any unreadable answer raises :class:`LandedRevisionUnknown`."""
        where = f"{self.repo}:{self.base_branch}"
        try:
            tip = await self.ask(self.forge.refs.branch_tip(self.base_branch))
        except ForgeRejected as exc:
            raise LandedRevisionUnknown(f"could not read {where}'s live tip: {exc}") from exc
        if tip is None:
            raise LandedRevisionUnknown(f"{where} does not exist, so it has no live tip")
        return tip

    async def verdict(self, ref: str) -> Verdict:
        """``ref``'s checks, degrading to ``None`` on any read the forge refused or did not answer."""
        try:
            return Verdict(await self.ask(self.forge.checks.checks(ref)))
        except (ForgeRejected, ForgeNotFound):
            return Verdict(None)
        except ForgeUnavailable as exc:
            self.run.hold(exc)
            return Verdict(None)

    def pr_title(self) -> str:
        """The visit's feature title, or the branch when none resolved, truncated to :data:`_PR_TITLE_MAX`."""
        title = self.run.visit.feature_title or self.branch
        if len(title) > _PR_TITLE_MAX:
            title = title[: _PR_TITLE_MAX - 1].rstrip() + "…"
        return title

    def pr_body(self) -> str:
        """A newly opened PR's body: the work items, the chunk id, and its board link."""
        visit = self.run.visit
        parts = ["Delivered by blizzard."]
        lines = self.run.item_lines()
        if lines:
            parts.append("Work items:\n" + "\n".join(f"- {line}" for line in lines))
        parts.append(f"Chunk: {visit.chunk.chunk_id}")
        if visit.public_url:
            parts.append(f"Board: {visit.public_url.rstrip('/')}/board/chunk/{visit.chunk.chunk_id}")
        return "\n\n".join(parts) + "\n"

    def merge_message(self) -> str:
        """The merge commit message: the title, then each work item's line."""
        title = self.run.visit.feature_title or f"blizzard: land {self.repo}"
        lines = self.run.item_lines()
        return f"{title}\n\n" + "\n".join(lines) if lines else title


@dataclass(frozen=True)
class _PullRequest:
    """One repository's change for the chunk's branch, and the sole owner of the forge's change calls.
    :attr:`change` is the change as of the last read."""

    target: _Target
    change: Change

    @classmethod
    async def of(cls, target: _Target) -> _PullRequest:
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
                        target.branch, target.base_branch, target.pr_title(), target.pr_body()
                    )
                )
            except (ForgeRejected, ForgeNotFound) as exc:
                existing = await cls._after_refused_open(target, exc)
        return await cls._record(await cls(target, existing).reread())

    @classmethod
    async def _after_refused_open(cls, target: _Target, refusal: Exception) -> Change:
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
    async def _record(cls, pull: _PullRequest) -> _PullRequest:
        """Persist the PR identity before a pass can wait on, reject or merge it — one marker per PR."""
        await pull.target.run.post(
            f"{_PR_MARKER_PREFIX}{pull.target.repo}/{pull.number}",
            json.dumps({"repo": pull.target.repo, "number": pull.number, "url": pull.url}),
        )
        return pull

    @staticmethod
    async def _listed(target: _Target, listing: ChangeListing) -> tuple[Change, ...]:
        try:
            return await target.ask(target.forge.changes.changes(listing, target.base_branch, target.branch))
        except ForgeRejected as exc:
            raise PullRequestLookupError(f"could not read {target.repo}'s {listing} changes: {exc}") from exc

    @classmethod
    async def _open_for_branch(cls, target: _Target) -> Change | None:
        listed = await cls._listed(target, ChangeListing.OPEN)
        return listed[0] if listed else None

    @classmethod
    async def _merged_for_branch(cls, target: _Target) -> Change | None:
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

    async def reread(self) -> _PullRequest:
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
                self.target.forge.changes.merge(self.number, revision, MergeMethod.MERGE, self.target.merge_message())
            )
        except (ForgeRejected, ForgeNotFound) as exc:
            refusal = str(exc)
        landed = await self.reread()
        if not landed.merged:
            raise MergeDidNotLand(refusal)
        if landed.change.merged_revision is None:
            raise LandedRevisionUnknown(f"{self} merged but no read carries its landed commit")
        return landed.change.merged_revision


async def gate_head(target: _Target, head: str) -> HeadGate:
    """Whether ``head`` is the target's submitted commit or descends from it through base merges alone. An
    answer it cannot prove refuses; a forge that did not answer raises :class:`ForgeUnavailable`."""
    submitted = target.commit
    if head == submitted:
        return HeadGate(())
    ahead = await target.compare(submitted, head)
    if ahead is None:
        return HeadGate((f"the forge would not compare {submitted}...{head}",))
    if ahead.status is ComparisonStatus.IDENTICAL:
        return HeadGate(())
    if ahead.status is not ComparisonStatus.AHEAD:
        return HeadGate((f"{head} does not descend from the submitted commit {submitted} (it was rewritten or reset)",))
    if ahead.commits_truncated:
        return HeadGate((f"the commit list between {submitted} and {head} is truncated",))
    chain, unreachable = first_parent_chain(ahead.commits, head, submitted)
    if unreachable is not None:
        return HeadGate((unreachable,))
    offenders: list[str] = []
    for commit in chain:
        sha = commit.revision
        if len(commit.parents) != 2:
            offenders.append(f"{sha} (not a merge of the base branch)")
            continue
        first, second = commit.parents
        if not await target.base_holds(second):
            offenders.append(f"{sha} (merges {second}, which the base branch does not hold)")
            continue
        merged = await target.compare(first, sha)
        base_side = await target.compare(first, second)
        merge_base = base_side.merge_base if base_side is not None else None
        if merged is None or base_side is None or merge_base is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
            continue
        feature_side = await target.compare(merge_base, first)
        if feature_side is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
        elif not contributes_only_base_change(merged, base_side, feature_side):
            offenders.append(f"{sha} (adds content beyond the base branch's change)")
    return HeadGate(tuple(offenders))


async def _rerun_once(pull: _PullRequest, check: Check, head_sha: str) -> bool | None:
    """Ask the forge once to re-run ``check``, then record its signature marker — side effect first: a pass
    ending before the marker is durable just re-runs it next pass, which is harmless to repeat. Whether the
    forge accepted; ``None`` when it did not answer, so the next pass asks again. A marker is written only for
    an accepted re-run, so a refusal never reads as one, and a marker write that fails is not fatal."""
    target = pull.target
    try:
        accepted = await target.ask(target.forge.checks.rerun_check(check.id))
    except (ForgeRejected, ForgeNotFound):
        return False
    except ForgeUnavailable as exc:
        target.run.hold(exc)
        return None
    if not accepted:
        return False
    try:
        await target.run.post(_rerun_marker(target.repo, check.name, head_sha), head_sha)
    except _NotRecorded as exc:
        target.run.say(f"re-run signature marker write failed (non-fatal): {exc}")
    return True


@dataclass(frozen=True)
class _Ready:
    """A PR the check stage cleared to merge, at the head it merges."""

    pull: _PullRequest
    head_sha: str


class Land:
    spec = LAND

    async def take(self, visit: Visit, forges: VisitForges, effects: IEffectSink) -> Pass:
        run = _Run(visit, effects)
        try:
            return await self._land(run, forges)
        except _NotRecorded as exc:
            run.say(f"marker write failed: {exc}")
            return run.answer(_FAILURE)
        except _LandingFenced as exc:
            run.say(f"the chunk moved on while it landed: {exc}; no other repository is touched")
            return run.answer(_FAILURE)
        except _Escalation as exc:
            run.say(f"escalated to a person: {exc}")
            return run.escalate(exc.escalation)
        except ForgeUnavailable as exc:
            run.hold(exc)
            run.say(f"the forge did not answer: {exc}; re-polling")
            return run.answer(_PENDING)

    @staticmethod
    def _targets(run: _Run, forges: VisitForges) -> list[_Target]:
        visit = run.visit
        rows, resolution = visit.git_commits.rows, visit.resolution
        return [
            _Target(
                run=run,
                repo=resolution.qualified[index],
                branch=row.data.partition(":")[0],
                commit=row.data.partition(":")[2],
                record=resolution.targets[index],
                forge=forges[RepositoryId(resolution.targets[index].record)],
            )
            for index, row in enumerate(rows)
        ]

    async def _land(self, run: _Run, forges: VisitForges) -> Pass:
        if not run.visit.git_commits.rows and run.visit.node.expects_git_commits:
            run.say(
                "no git commits to deliver: this chunk submitted no git_commit artifact, so there is nothing to "
                "open a PR for. A delivery node reached with an empty commit set is a failure, not a landing — "
                "check that the nodes before this one declared their commits (`blizzard runner artifact "
                "commit`) and that each verified."
            )
            return run.answer(_FAILURE)
        already = run.visit.marker_names()
        pending = [target for target in self._targets(run, forges) if target.marker() not in already]
        if not pending:
            return run.answer(_LANDED)

        wait = False
        # --- gate stage: every head is proven before any repo is routed, so a foreign head fires nothing.
        resolved: list[_PullRequest] = []
        foreign: list[dict[str, Any]] = []
        for target in pending:
            try:
                pull = await _PullRequest.of(target)
            except NothingToLand as exc:
                # Nothing to merge, and no poll changes that: the landing stops this repo being pending.
                run.say(f"{exc} — nothing to land")
                await run.landed(target, exc.landed_sha)
                continue
            except (LandedRevisionUnknown, PullRequestOpenError, PullRequestLookupError) as exc:
                # A degraded read or a create hiccup is worth another poll — never "not merged".
                run.say(str(exc))
                wait = True
                continue
            except ForgeUnavailable as exc:
                run.hold(exc)
                run.say(f"{target.repo}: the forge did not answer: {exc}; re-polling")
                wait = True
                continue
            # A merged PR is gated too: one recovered by the live-tip fallback may carry foreign content.
            try:
                gate = await gate_head(target, pull.head_sha)
            except ForgeUnavailable as exc:
                run.hold(exc)
                run.say(f"{pull}: the forge did not answer: {exc}; re-polling")
                wait = True
                continue
            if not gate.admitted():
                run.say(f"{pull}'s head {pull.head_sha} is not the submitted commit plus base merges")
                foreign.append(
                    pull.record(FOREIGN, submitted=target.commit, head=pull.head_sha, offenders=gate.offenders)
                )
                continue
            resolved.append(pull)

        if foreign:
            # Unguarded: unwritten findings leave the repair worker nothing to read.
            rendered = Findings(tuple(foreign)).render()
            await run.post(_FINDINGS_NAME, rendered)
            await run.post(_FOREIGN_FINDINGS_NAME, rendered)
            return run.answer(_FAILURE)

        # --- check stage: no repo is merged unless ALL are ready (chunk atomicity), and the loop never
        #     short-circuits on a failure, so findings accumulate together.
        ready: list[_Ready] = []
        failures: list[dict[str, Any]] = []
        inherited_failures: list[dict[str, Any]] = []
        wait_records: list[dict[str, Any]] = []
        try:
            for pull in resolved:
                head_sha = pull.head_sha
                state = pull.mergeability
                # A verdict is only worth reading for the states merge eligibility turns on — read once.
                verdict = await pull.target.verdict(head_sha) if state in CI_WATCH_STATES else None
                decision = Route(state, merged=pull.merged, verdict=verdict).decision
                if decision == BOUNCE:
                    raise _Conflict(f"{pull} is dirty (a real merge conflict)")
                if decision == UPDATE:
                    # Any refusal other than a named conflict waits — the next poll's state is authoritative.
                    try:
                        update = await pull.update_from_base(head_sha)
                    except ForgeUnavailable as exc:
                        run.hold(exc)
                        run.say(f"{pull} behind — the forge did not answer its update: {exc}; re-polling")
                        wait = True
                        continue
                    if update is not None and update.outcome is UpdateOutcome.CONFLICT:
                        raise _Conflict(f"{pull} could not be updated from its base without a conflict")
                    answered = update.outcome if update is not None else "the change was not found"
                    run.say(f"{pull} behind — update from its base requested ({answered}); re-polling")
                    wait = True
                    continue
                if decision == WAIT:
                    if verdict is not None:
                        # A degraded CI read falls through to the plain wait below.
                        if verdict.decision == FAILED:
                            base = await pull.target.verdict(pull.target.base_branch)
                            checks = verdict.failure_rows(base)
                            if not all(inherited(check["base_red"]) for check in checks):
                                # Any owned failure charges this chunk; inherited ones on the same repo
                                # accompany it in one diagnosis.
                                failures.append(pull.record(FAILED, checks=checks))
                                run.say(f"{pull} has a terminal CI check failure — will not re-poll")
                                continue
                            # Every failing check is inherited from the base: re-run each once (a forge that
                            # cannot re-run counts as re-run) before charging a repair.
                            repo = pull.target.repo
                            rerunnable = Capability.CHECKS_RERUN in pull.target.forge.offered
                            unsigned = [
                                check
                                for check in verdict.failing
                                if rerunnable and _rerun_marker(repo, check.name, head_sha) not in already
                            ]
                            if unsigned:
                                accepted = [await _rerun_once(pull, check, head_sha) for check in unsigned]
                                if False in accepted:
                                    # The forge will not re-run a check for this credential: no re-run is
                                    # coming, so the failure routes as an owned one would.
                                    failures.append(pull.record(FAILED, checks=checks))
                                    run.say(f"{pull}'s failing checks could not be re-run — will not re-poll")
                                    continue
                                run.say(
                                    f"{pull}'s failing checks are inherited from the base — re-requested once; "
                                    "re-polling"
                                )
                                wait = True
                                continue
                            inherited_failures.append(
                                pull.record(
                                    INHERITED_FAILURE,
                                    base_branch=pull.target.base_branch,
                                    checks=[
                                        {
                                            "name": check.name,
                                            "conclusion": check.detail,
                                            "details_url": check.details_url,
                                            "signature": _rerun_marker(repo, check.name, head_sha),
                                        }
                                        for check in verdict.failing
                                    ],
                                )
                            )
                            run.say(
                                f"{pull}'s failing checks are still inherited from the base after a re-run — not "
                                "this chunk's defect; routing to repair the base"
                            )
                            continue
                        if verdict.substantive:
                            wait_records.append(pull.record(WAIT, checks=verdict.running_rows()))
                    run.say(f"{pull} is {state} — not cleanly mergeable yet; re-polling")
                    wait = True
                    continue
                # Already merged, or a green verdict at a mergeable or blocked head — eligible.
                ready.append(_Ready(pull, head_sha))
        except _Conflict as exc:
            run.say(f"conflict: {exc}")
            return run.answer(_CONFLICT)

        if failures:
            # Nothing merges (chunk atomicity); unguarded, since unwritten findings leave `resolve` nothing.
            await run.post(_FINDINGS_NAME, Findings(tuple(failures)).render())
            return run.answer(_FAILURE)

        if inherited_failures:
            # Nothing merges, and unguarded as above: the repair charge reads these findings.
            await run.post(_FINDINGS_NAME, Findings(tuple(inherited_failures)).render())
            return run.answer(INHERITED_FAILURE)

        if wait:
            # Merge nothing. Findings are re-written every poll, so a refused write here only waits.
            if wait_records:
                try:
                    await run.post(_FINDINGS_NAME, Findings(tuple(wait_records)).render())
                except _NotRecorded as exc:
                    run.say(f"delivery-findings write failed (wait path, non-fatal): {exc}")
            return run.answer(_PENDING)

        # --- merge stage: merge the CURRENT head sha, which a self-heal update-branch may have advanced past
        #     the submitted commit; each landing is durable before the next repository is touched.
        for item in ready:
            try:
                landed_sha = await item.pull.merge(item.head_sha)
            except MergeDidNotLand as exc:
                # Not an already-merged earlier pass (`merge` absorbs that) — a race worth re-polling.
                run.say(f"merge of {item.pull} did not land ({exc}); will re-poll")
                return run.answer(_PENDING)
            except (LandedRevisionUnknown, PullRequestLookupError) as exc:
                # Merged, but no read named its commit — the next poll recovers it as already merged.
                run.say(f"{exc}; will re-poll")
                return run.answer(_PENDING)
            await run.landed(item.pull.target, landed_sha)

        return run.answer(_LANDED)
