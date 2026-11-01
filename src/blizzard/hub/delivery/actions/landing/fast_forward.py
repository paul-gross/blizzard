"""Prove a revision's checks before advancing the base without a change request."""

from __future__ import annotations

from blizzard.foundation.crash import crashpoint
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.hub.delivery.actions.landing.base_merge import contributes_only_base_change
from blizzard.hub.delivery.actions.landing.checks import FOREIGN, failed_checks, read_verdict
from blizzard.hub.delivery.actions.landing.head_gate import gate_head
from blizzard.hub.delivery.actions.landing.repo import (
    Admitted,
    Conflict,
    Failure,
    ForgeEscalation,
    Landed,
    LandedRevisionUnknown,
    NeedsPerson,
    Ready,
    RepoLanding,
    RepoState,
    Waiting,
)
from blizzard.hub.domain.actions.effects import ActionEscalation
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.forge.landing import ComparisonStatus, ForgeNotFound, ForgeRejected, ForgeUnavailable

_CREATED = crashpoint("policy.after-create-ref.before-merge-into", "landing branch created before candidate merge")
_CANDIDATE = crashpoint("policy.after-merge-into.before-advance", "candidate created before base advance")
_ADVANCED = crashpoint("policy.after-advance.before-marker", "base advanced before landing record")
_RECORDED = crashpoint("policy.after-marker.before-delete-ref", "landing recorded before candidate cleanup")
_DIVERGED = frozenset({Capability.REFS_CREATE, Capability.REFS_DELETE, Capability.BRANCHES_MERGE})


def _person(repo: RepoLanding, cause: EscalationCause, detail: str) -> NeedsPerson:
    return NeedsPerson(ActionEscalation(cause, f"{repo.repo} on forge {repo.forge_label}: {detail}"))


def _intent(repo: RepoLanding, candidate: str = "") -> str:
    return f"fast-forward-candidate/{repo.repo}/{candidate}"


async def _holds(repo: RepoLanding, ancestor: str, descendant: str) -> bool | None:
    comparison = await repo.compare(ancestor, descendant)
    if comparison is None:
        return None
    return comparison.status in {ComparisonStatus.IDENTICAL, ComparisonStatus.AHEAD}


async def _candidate_proven(repo: RepoLanding, base: str, head: str, candidate: str) -> bool | None:
    """A single base-first merge of exactly the gated work, including its content."""
    merged = await repo.compare(base, candidate)
    work = await repo.compare(base, head)
    if merged is None or work is None or work.merge_base is None:
        return None
    base_side = await repo.compare(work.merge_base, base)
    if base_side is None:
        return None
    if merged.commits_truncated:
        return None
    commit = next((item for item in merged.commits if item.revision == candidate), None)
    return (
        commit is not None and commit.parents == (base, head) and contributes_only_base_change(merged, work, base_side)
    )


async def cleanup(repo: RepoLanding, revision: str) -> None:
    """Best-effort cleanup only after the landing effect is durable, and only of that candidate."""
    _RECORDED.reached()
    if Capability.REFS_DELETE not in repo.forge.offered:
        return
    branch = f"{repo.branch}-landing"
    try:
        if await repo.ask(repo.forge.refs.branch_tip(branch)) == revision:
            await repo.ask(repo.forge.refs.delete_ref(branch))
    except (ForgeEscalation, ForgeRejected, ForgeNotFound, ForgeUnavailable) as exc:
        repo.run.say(f"{repo.repo}: could not clean up {branch}: {exc}")


class FastForward:
    async def step(self, repo: RepoLanding) -> RepoState | Admitted:
        try:
            head = await repo.ask(repo.forge.refs.branch_tip(repo.branch))
            if head is None:
                return Waiting(f"{repo.repo}:{repo.branch} has no readable tip")
            gate = await gate_head(repo, head)
            if not gate.admitted():
                finding = {
                    "repo": repo.repo,
                    "number": "-",
                    "url": repo.branch,
                    "decision": FOREIGN,
                    "submitted": repo.commit,
                    "head": head,
                    "offenders": gate.offenders,
                }
                return Failure((finding,), foreign_head=True)
            return Admitted(lambda: self._prepare(repo, head))
        except ForgeEscalation as exc:
            return NeedsPerson(exc.escalation)
        except ForgeUnavailable as exc:
            repo.run.hold(exc)
            return Waiting(str(exc))

    async def _prepare(self, repo: RepoLanding, head: str) -> RepoState:
        try:
            return await self._candidate(repo, head)
        except ForgeEscalation as exc:
            return NeedsPerson(exc.escalation)
        except ForgeUnavailable as exc:
            repo.run.hold(exc)
            return Waiting(str(exc))
        except (ForgeRejected, ForgeNotFound, LandedRevisionUnknown) as exc:
            if isinstance(exc, ForgeRejected) and exc.reason == ForgeRejected.PROTECTED:
                return _person(repo, EscalationCause.BRANCH_PROTECTED, f"branch {repo.branch}-landing: {exc}")
            if isinstance(exc, ForgeRejected) and exc.reason == ForgeRejected.CONFLICT:
                return Conflict(f"{repo.repo}: candidate merge conflicts: {exc}")
            return Waiting(str(exc))

    async def _candidate(self, repo: RepoLanding, head: str) -> RepoState:
        base = await repo.base_tip()
        # The intent is written only after proof, before advancing. A later base may have moved past it.
        for marker in reversed(repo.run.visit.markers):
            if marker.name.startswith(_intent(repo)):
                recovered = await _holds(repo, marker.data, base)
                if recovered is None:
                    return Waiting(f"{repo.repo}: candidate recovery comparison not readable")
                if recovered:
                    return Landed(marker.data)
        comparison = await repo.compare(base, head)
        if comparison is None:
            return Waiting(f"{repo.repo}: cannot compare base and work")
        if comparison.status in {ComparisonStatus.IDENTICAL, ComparisonStatus.BEHIND}:
            return Landed(head)
        candidate = head
        if comparison.status is ComparisonStatus.DIVERGED:
            missing = _DIVERGED - repo.forge.offered
            if missing:
                detail = f"diverged landing needs {', '.join(sorted(missing))}"
                return _person(repo, EscalationCause.FORGE_CAPABILITY_MISSING, detail)
            branch = f"{repo.branch}-landing"
            tip = await repo.ask(repo.forge.refs.branch_tip(branch))
            if tip is not None:
                proven = await _candidate_proven(repo, base, head, tip)
                if proven is None:
                    return Waiting(f"{repo.repo}: candidate proof not readable")
                if not proven:
                    await repo.ask(repo.forge.refs.delete_ref(branch))
                    tip = None
            if tip is None:
                await repo.ask(repo.forge.refs.create_ref(branch, base))
                _CREATED.reached()
                try:
                    tip = await repo.ask(repo.forge.refs.merge_into(branch, head, f"blizzard: land {repo.repo}"))
                except ForgeRejected as exc:
                    if exc.reason == ForgeRejected.CONFLICT:
                        await repo.ask(repo.forge.refs.delete_ref(branch))
                    raise
                if tip is None:
                    tip = await repo.ask(repo.forge.refs.branch_tip(branch))
                _CANDIDATE.reached()
            if tip is None:
                return Waiting(f"{repo.repo}: candidate not readable")
            proven = await _candidate_proven(repo, base, head, tip)
            if proven is not True:
                return Waiting(f"{repo.repo}: candidate merge could not be proven")
            candidate = tip
        verdict = await read_verdict(repo, candidate)
        if verdict.failing:
            base_verdict = await read_verdict(repo, base)
            return await failed_checks(
                repo,
                candidate,
                verdict,
                verdict.failure_rows(base_verdict),
                {"repo": repo.repo, "number": "-", "url": repo.branch},
            )
        if not verdict.green:
            return Waiting(f"{repo.repo}: candidate checks not green")

        async def advance() -> Landed | Waiting | NeedsPerson:
            await repo.run.post(_intent(repo, candidate), candidate)
            try:
                await repo.ask(repo.forge.refs.advance_ref(repo.base_branch, base, candidate))
                _ADVANCED.reached()
                return Landed(candidate)
            except ForgeEscalation as exc:
                return NeedsPerson(exc.escalation)
            except ForgeRejected as exc:
                if exc.reason == ForgeRejected.PROTECTED:
                    return _person(repo, EscalationCause.BRANCH_PROTECTED, f"branch {repo.base_branch}: {exc}")
                return Waiting(f"{repo.repo}: base advance refused: {exc}")
            except ForgeUnavailable as exc:
                repo.run.hold(exc)
                return Waiting(str(exc))

        return Ready(advance)
