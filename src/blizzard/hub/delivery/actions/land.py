"""``land`` — lands a chunk's commits on each repository's base branch through a pull request, self-healing:
``behind`` updates the branch, ``dirty`` answers ``conflict``, a head beyond the submitted commit and its base
merges answers ``failure``, and anything else unsettled waits (``pending``). Nothing merges unless every
repository is ready; each merge is then handed over as a :class:`LandingRecord` and awaited before the next
repository is touched. A pass reads its node's markers to skip what earlier passes did, and speaks to each
repository only through its :class:`IGitHubHandle`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from blizzard.hub.delivery.actions.base_merge import contributes_only_base_change, merge_base_of
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
from blizzard.hub.delivery.forge_handle import ForgeUnreachable, IGitHubHandle, VisitForges
from blizzard.hub.domain.actions.effects import EffectStatus, IEffectSink, LandingRecord, Marker, Pass
from blizzard.hub.domain.actions.visit import Visit
from blizzard.hub.domain.config.repositories import ResolvedRepository
from blizzard.hub.domain.graph.actions import LAND

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

# Bounds the closed-pull scan a merged-PR lookup does per repo (1000 PRs against one branch).
_CLOSED_PULLS_PAGE_MAX = 10


class ForgeReadDegraded(Exception):
    """A forge read got no answer (no connection, or a 5xx); another pass may."""


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
    """A merge did not land and a re-read confirms the PR is still open; :attr:`result` is the refusal."""

    def __init__(self, result: Any) -> None:
        super().__init__(str(result))
        self.result = result


class _Conflict(Exception):
    """Aborts the check stage as a real conflict — nothing has been merged."""


class _NotRecorded(Exception):
    """A marker the pass needs durable was not recorded: the chunk moved past the visit."""


class _LandingFenced(Exception):
    """A merge's landing record was written past the fence: the chunk moved on while it landed."""


def _rerun_marker(repo: str, name: str, head_sha: str) -> str:
    return f"{_RERUN_MARKER_PREFIX}{repo}/{name}/{head_sha}"


@dataclass
class _Run:
    """One pass's channels out: its sink, and the log lines its ``hub-log.land`` artifact carries."""

    visit: Visit
    effects: IEffectSink
    log: list[str] = field(default_factory=list)

    def say(self, line: str) -> None:
        self.log.append(line)

    def answer(self, choice: str) -> Pass:
        return Pass(choice=choice, log=tuple(self.log))

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
    commit, its resolved record, and the handle on its forge."""

    run: _Run
    repo: str
    branch: str
    commit: str
    record: ResolvedRepository
    forge: IGitHubHandle

    @property
    def base_branch(self) -> str:
        return self.record.base_branch

    def marker(self) -> str:
        return f"{_MARKER_PREFIX}{self.repo}"

    async def compare(self, base: str, head: str) -> dict[str, Any] | None:
        """The forge's ``base...head`` comparison; ``None`` when answered unusably, and a read with no answer
        (unreachable, a 5xx) raises :class:`ForgeReadDegraded`."""
        try:
            reply = await self.forge.compare(self.repo, base, head)
        except ForgeUnreachable as exc:
            raise ForgeReadDegraded(f"could not read {self.repo}'s compare {base}...{head}: {exc}") from exc
        if reply.status >= 500:
            raise ForgeReadDegraded(f"could not read {self.repo}'s compare {base}...{head} (HTTP {reply.status})")
        if reply.status != 200 or not isinstance(reply.payload, dict):
            return None
        return reply.payload

    async def base_holds(self, ref: str) -> bool:
        """Whether the base branch already holds every commit on ``ref``; a degraded read raises."""
        payload = await self.compare(self.base_branch, ref)
        return payload is not None and payload.get("status") in {"identical", "behind"}

    async def contains(self, ref: str) -> bool:
        """Whether the base branch already holds every commit on ``ref``, read from the forge's own
        comparison; any unreadable answer is ``False`` — never "there was nothing to land"."""
        try:
            return await self.base_holds(ref)
        except ForgeReadDegraded:
            return False

    async def base_tip(self) -> str:
        """The base branch's live tip; any unreadable answer raises :class:`LandedRevisionUnknown`."""
        where = f"{self.repo}:{self.base_branch}"
        try:
            reply = await self.forge.branch(self.repo, self.base_branch)
        except ForgeUnreachable as exc:
            raise LandedRevisionUnknown(f"could not read {where}'s live tip: {exc}") from exc
        if reply.status != 200 or not isinstance(reply.payload, dict):
            raise LandedRevisionUnknown(f"could not read {where}'s live tip (HTTP {reply.status})")
        tip = (reply.payload.get("object") or {}).get("sha")
        if not isinstance(tip, str) or not tip:
            raise LandedRevisionUnknown(f"{where}'s live tip read came back without a sha")
        return tip

    async def verdict(self, ref: str) -> Verdict:
        """``ref``'s check runs, degrading to ``None`` on any read failure."""
        try:
            reply = await self.forge.check_runs(self.repo, ref)
        except ForgeUnreachable:
            return Verdict(None)
        return Verdict.of(reply.status, reply.payload)

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
    """One repository's PR for the chunk's branch, and the sole owner of the forge's ``pulls`` calls.
    :attr:`body` is the PR as of the last read."""

    target: _Target
    number: int
    body: dict[str, Any]

    @classmethod
    async def of(cls, target: _Target) -> _PullRequest:
        """The PR for the target's branch: an already-merged one first, so a re-visit recovers a merge even
        when its record was interrupted — then the open one, opening one first when neither exists, then
        read live. Raises :class:`PullRequestOpenError` or :class:`PullRequestLookupError` on a hiccup."""
        merged = await cls._merged_for_branch(target)
        if merged is not None:
            return await cls._record(await cls(target, int(merged["number"]), {}).reread())
        listed = (await target.forge.open_pulls(target.repo)).payload
        existing = next(
            (p for p in (listed if isinstance(listed, list) else []) if p.get("head", {}).get("ref") == target.branch),
            None,
        )
        if existing is None:
            reply = await target.forge.open_pull(
                target.repo, title=target.pr_title(), head=target.branch, base=target.base_branch, body=target.pr_body()
            )
            if reply.status != 201:
                if await target.contains(target.branch):
                    raise NothingToLand(
                        f"{target.repo}:{target.branch} adds no commit {target.base_branch} does not already have",
                        await target.base_tip(),
                    )
                raise PullRequestOpenError(f"could not open a PR for {target.repo}:{target.branch}: {reply.payload}")
            existing = reply.payload
        return await cls._record(await cls(target, int(existing["number"]), {}).reread())

    @classmethod
    async def _record(cls, pull: _PullRequest) -> _PullRequest:
        """Persist the PR identity before a pass can wait on, reject or merge it — one marker per PR."""
        await pull.target.run.post(
            f"{_PR_MARKER_PREFIX}{pull.target.repo}/{pull.number}",
            json.dumps({"repo": pull.target.repo, "number": pull.number, "url": pull.url}),
        )
        return pull

    @staticmethod
    async def _merged_for_branch(target: _Target) -> dict[str, Any] | None:
        """The already-merged PR that landed the submitted commit — else the branch's live tip — into the
        target's own base, or ``None`` when the read confirms there is none: never a same-name PR merged into
        a different base, and never a degraded read, which raises :class:`PullRequestLookupError`."""
        repo, branch, base = target.repo, target.branch, target.base_branch
        candidates: list[dict[str, Any]] = []
        for page in range(1, _CLOSED_PULLS_PAGE_MAX + 1):
            reply = await target.forge.closed_pulls(repo, base=base, page=page)
            listed = reply.payload
            if reply.status != 200 or not isinstance(listed, list):
                raise PullRequestLookupError(f"could not read {repo}'s closed pull list (HTTP {reply.status})")
            candidates.extend(
                p
                for p in listed
                if p.get("head", {}).get("ref") == branch
                and p.get("base", {}).get("ref") == base
                and p.get("merged_at") is not None
            )
            if len(listed) < 100:
                break
        if not candidates:
            return None
        recorded = next((p for p in candidates if p.get("head", {}).get("sha") == target.commit), None)
        if recorded is not None:
            return recorded
        reply = await target.forge.branch(repo, branch)
        if reply.status == 404:
            return None  # the forge deleted the head branch on merge: the ref is gone, not unreadable
        if reply.status != 200 or not isinstance(reply.payload, dict):
            raise PullRequestLookupError(f"could not read {repo}:{branch}'s live tip (HTTP {reply.status})")
        live_sha = (reply.payload.get("object") or {}).get("sha")
        if not isinstance(live_sha, str) or not live_sha:
            raise PullRequestLookupError(f"{repo}:{branch}'s live tip read came back without a sha")
        return next((p for p in candidates if p.get("head", {}).get("sha") == live_sha), None)

    def __str__(self) -> str:
        return f"{self.target.repo}#{self.number}"

    @property
    def merged(self) -> bool:
        return bool(self.body.get("merged"))

    @property
    def mergeable_state(self) -> str | None:
        return self.body.get("mergeable_state")

    @property
    def head_sha(self) -> str | None:  # ast-grep-ignore: bzh:property-delegates
        return (self.body.get("head") or {}).get("sha")

    @property
    def url(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return self.body.get("html_url") or ""

    def record(self, decision: str, **fields: Any) -> dict[str, Any]:
        """A findings record for this PR."""
        return {"repo": self.target.repo, "number": self.number, "url": self.url, "decision": decision, **fields}

    async def reread(self) -> _PullRequest:
        reply = await self.target.forge.pull(self.target.repo, self.number)
        return replace(self, body=reply.payload if isinstance(reply.payload, dict) else {})

    async def update_branch(self, expected_head_sha: str) -> tuple[int, str]:
        """Ask the forge to merge the base branch in — guarded on ``expected_head_sha``, so updates never
        stack."""
        reply = await self.target.forge.update_branch(
            self.target.repo, self.number, expected_head_sha=expected_head_sha
        )
        payload = reply.payload
        return reply.status, (payload.get("message", "") if isinstance(payload, dict) else "")

    async def merge(self, sha: str) -> str:
        """Merge at ``sha`` with a merge commit and return the landed commit, from the response or a reread.
        Already-merged PRs are not remerged; an unconfirmed merge raises :class:`MergeDidNotLand`, an unknown
        landed revision :class:`LandedRevisionUnknown`."""
        reply = await self.target.forge.merge(
            self.target.repo, self.number, sha=sha, commit_message=self.target.merge_message(), method="merge"
        )
        result = reply.payload
        if reply.status == 200 and isinstance(result, dict) and result.get("merged") and result.get("sha"):
            return result["sha"]
        landed = await self.reread()
        if not landed.merged:
            raise MergeDidNotLand(result)
        merge_commit = landed.body.get("merge_commit_sha")
        if not merge_commit:
            raise LandedRevisionUnknown(f"{self} merged but neither read carries its landed commit")
        return merge_commit


async def gate_head(target: _Target, head: str) -> HeadGate:
    """Whether ``head`` is the target's submitted commit or descends from it through base merges alone. An
    answer it cannot prove refuses; a read that got none raises :class:`ForgeReadDegraded`."""
    submitted = target.commit
    if head == submitted:
        return HeadGate(())
    ahead = await target.compare(submitted, head)
    if ahead is None:
        return HeadGate((f"the forge would not compare {submitted}...{head}",))
    if ahead.get("status") == "identical":
        return HeadGate(())
    if ahead.get("status") != "ahead":
        return HeadGate((f"{head} does not descend from the submitted commit {submitted} (it was rewritten or reset)",))
    commits = ahead.get("commits")
    total = ahead.get("total_commits", ahead.get("ahead_by"))
    if not isinstance(commits, list) or not isinstance(total, int) or len(commits) != total:
        return HeadGate((f"the commit list between {submitted} and {head} is truncated or unreadable",))
    chain, unreachable = first_parent_chain(commits, head, submitted)
    if unreachable is not None:
        return HeadGate((unreachable,))
    offenders: list[str] = []
    for commit in chain:
        sha = commit["sha"]
        parents = [p["sha"] for p in commit["parents"] if isinstance(p, dict) and isinstance(p.get("sha"), str)]
        if len(parents) != 2 or len(commit["parents"]) != 2:
            offenders.append(f"{sha} (not a merge of the base branch)")
            continue
        first, second = parents
        if not await target.base_holds(second):
            offenders.append(f"{sha} (merges {second}, which the base branch does not hold)")
            continue
        merged = await target.compare(first, sha)
        base_side = await target.compare(first, second)
        merge_base = merge_base_of(base_side) if base_side is not None else None
        if merged is None or base_side is None or merge_base is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
            continue
        feature_side = await target.compare(merge_base, first)
        if feature_side is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
        elif not contributes_only_base_change(merged, base_side, feature_side):
            offenders.append(f"{sha} (adds content beyond the base branch's change)")
    return HeadGate(tuple(offenders))


async def _rerequest_once(pull: _PullRequest, check: dict[str, Any], head_sha: str) -> None:
    """Fire the check-run rerequest once for ``check``, then record its signature marker — side effect
    first: a pass ending before the marker is durable just re-fires the rerequest next pass, which is
    harmless to repeat. Neither is fatal; the marker is written only when the forge answers 2xx, so a
    refusal never reads as a re-run."""
    check_id = check.get("id")
    if check_id is None:
        return
    target = pull.target
    try:
        reply = await target.forge.rerequest_check_run(target.repo, check_id)
    except ForgeUnreachable:
        return
    if not 200 <= reply.status < 300:
        return
    try:
        await target.run.post(_rerun_marker(target.repo, check.get("name", ""), head_sha), head_sha)
    except _NotRecorded as exc:
        target.run.say(f"re-run signature marker write failed (non-fatal): {exc}")


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
                forge=forges[resolution.targets[index].record],
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
            if pull.head_sha:
                # A merged PR is gated too: one recovered by the live-tip fallback may carry foreign content.
                try:
                    gate = await gate_head(target, pull.head_sha)
                except ForgeReadDegraded as exc:
                    run.say(f"{pull}: {exc}; re-polling")
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
                head_sha = pull.head_sha or pull.target.commit
                state = pull.mergeable_state
                # A verdict is only worth reading for the states merge eligibility turns on — read once.
                verdict = await pull.target.verdict(head_sha) if state in CI_WATCH_STATES else None
                decision = Route(state, merged=pull.merged, verdict=verdict).decision
                if decision == BOUNCE:
                    raise _Conflict(f"{pull} is dirty (a real merge conflict)")
                if decision == UPDATE:
                    # Any refusal other than a named conflict waits — the next poll's state is authoritative.
                    ustatus, message = await pull.update_branch(head_sha)
                    if ustatus == 422 and "conflict" in message.lower():
                        raise _Conflict(f"{pull} update-branch reported a conflict: {message}")
                    run.say(f"{pull} behind — update-branch requested (HTTP {ustatus}); re-polling")
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
                            # Every failing check is inherited from the base: re-run each once before
                            # charging a repair; one already re-run at this head and still red is confirmed.
                            repo = pull.target.repo
                            unsigned = [
                                check
                                for check in verdict.failing
                                if _rerun_marker(repo, check.get("name", ""), head_sha) not in already
                            ]
                            if unsigned:
                                for check in unsigned:
                                    await _rerequest_once(pull, check, head_sha)
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
                                            "name": check.get("name"),
                                            "conclusion": check.get("conclusion"),
                                            "details_url": check.get("details_url"),
                                            "signature": _rerun_marker(repo, check.get("name", ""), head_sha),
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
                # Already merged, or a green verdict at a clean/blocked/unstable head — eligible.
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
                run.say(f"merge of {item.pull} did not land ({exc.result}); will re-poll")
                return run.answer(_PENDING)
            except LandedRevisionUnknown as exc:
                # Merged, but no read named its commit — the next poll recovers it as already merged.
                run.say(f"{exc}; will re-poll")
                return run.answer(_PENDING)
            await run.landed(item.pull.target, landed_sha)

        return run.answer(_LANDED)
