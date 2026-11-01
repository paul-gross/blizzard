"""How every landing policy reads checks: where one repository's change routes, how its checks classify, the
inherited-failure rule and its one-time re-run, and the ``delivery-findings`` a resolve worker reads."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from blizzard.foundation.roles import domain_model
from blizzard.hub.delivery.actions.landing.repo import Failure, Inherited, NotRecorded, RepoLanding, RepoState, Waiting
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.forge.landing import (
    Check,
    CheckOutcome,
    ForgeNotFound,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
    RerunOutcome,
)

# Pure routing decisions (what to do with one repo after reading its live change).
PUSH = "push"  # mergeable (or already merged) — eligible for the merge stage
WAIT = "wait"  # pending / draft / required-checks-not-green / … — re-poll, no side effect
UPDATE = "update"  # behind — bring it up to its base, then re-poll
BOUNCE = "bounce"  # conflicting — a real content conflict, kick back to build
FAILED = "failed"  # a check completed with a failing outcome — never re-poll
FOREIGN = "foreign-head"  # the change's head carries something beyond the submitted commit and base merges
# A findings record's decision when every failing check is inherited — the answer's own name.
INHERITED_FAILURE = "inherited-failure"

_GREEN_OUTCOMES = {CheckOutcome.PASSED, CheckOutcome.NEUTRAL}

#: Mergeabilities whose merge eligibility needs a live check verdict, not the mergeability alone.
CI_WATCH_STATES = {Mergeability.MERGEABLE, Mergeability.BLOCKED}


@domain_model
@dataclass(frozen=True)
class Verdict:
    """One ref's live checks, classified. ``None`` is a degraded read — it waits, exactly like a still-running
    one."""

    checks: tuple[Check, ...] | None

    @property
    def failing(self) -> list[Check]:  # ast-grep-ignore: bzh:property-delegates
        return [check for check in self.checks or () if check.outcome is CheckOutcome.FAILED]

    @property
    def pending(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """Whether some check on this ref has not concluded."""
        return any(check.outcome is CheckOutcome.PENDING for check in self.checks or ())

    @property
    def decision(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return FAILED if self.failing else WAIT

    @property
    def substantive(self) -> bool:
        """Whether this read says anything: a zero-check read is not worth findings."""
        return bool(self.checks)

    @property
    def green(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """The merge-eligibility bar: at least one check, and every one of them passed or neutral — a degraded
        or empty read is never green."""
        return bool(self.checks) and all(check.outcome in _GREEN_OUTCOMES for check in self.checks or ())

    def red(self, name: str) -> bool | None:
        """Whether this ref's own check named ``name`` failed — ``None`` on a degraded read, ``False`` when read
        but carrying no failed check of that name."""
        if self.checks is None:
            return None
        for check in self.checks:
            if check.name == name:
                return check.outcome is CheckOutcome.FAILED
        return False

    def failure_rows(self, base: Verdict) -> list[dict[str, Any]]:
        return [
            {
                "name": check.name,
                "conclusion": check.detail,
                "details_url": check.details_url,
                "base_red": base.red(check.name),
            }
            for check in self.failing
        ]

    def running_rows(self) -> list[dict[str, Any]]:
        return [{"name": check.name, "status": check.detail} for check in self.checks or ()]


@domain_model
@dataclass(frozen=True)
class Route:
    """Where one repo goes, from its change's live ``(merged, mergeability, verdict)``. Already-merged and
    conflicting/behind never need a verdict; a mergeable or blocked head pushes only on a green one, else
    waits."""

    mergeability: Mergeability
    merged: bool = False
    verdict: Verdict | None = None

    @property
    def decision(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if self.merged:
            return PUSH
        if self.mergeability is Mergeability.CONFLICTING:
            return BOUNCE
        if self.mergeability is Mergeability.BEHIND:
            return UPDATE
        if self.mergeability in CI_WATCH_STATES:
            return PUSH if self.verdict is not None and self.verdict.green else WAIT
        return WAIT


def inherited(base_red: bool | None) -> bool:
    """Whether a head-failing check is inherited from the base, given the base's own reading of that same
    check name; a degraded base read (``None``) is not inherited
    (``tests/test_land_action.py::test_a_degraded_base_read_charges_a_head_failure_to_the_chunk``)."""
    return base_red is True


_RERUN_MARKER_PREFIX = "ci-rerun/"


def rerun_marker(repo: str, name: str, head_sha: str) -> str:
    """The marker one inherited failure was re-run under — one per (repo, check name, head sha) — read back on a
    re-visit."""
    return f"{_RERUN_MARKER_PREFIX}{repo}/{name}/{head_sha}"


async def read_verdict(repo: RepoLanding, ref: str) -> Verdict:
    """``ref``'s checks, degrading to ``None`` on any read the forge refused or did not answer."""
    try:
        return Verdict(await repo.ask(repo.forge.checks.checks(ref)))
    except (ForgeRejected, ForgeNotFound):
        return Verdict(None)
    except ForgeUnavailable as exc:
        repo.run.hold(exc)
        return Verdict(None)


async def rerun_once(repo: RepoLanding, check: Check, head_sha: str) -> RerunOutcome | None:
    """Ask the forge once to re-run ``check``, then record its signature marker when it accepted. The forge's
    outcome; ``None`` when it did not answer (pinned by
    ``tests/test_land_action.py::test_a_refused_re_run_writes_no_rerun_marker_and_routes_failure``). A refusal is
    logged with the forge's own status and message."""
    try:
        rerun = await repo.ask(repo.forge.checks.rerun_check(check.id))
    except (ForgeRejected, ForgeNotFound) as exc:
        repo.run.say(f"{repo.repo}'s re-run of {check.name} was refused: {exc}")
        return RerunOutcome.REFUSED
    except ForgeUnavailable as exc:
        repo.run.hold(exc)
        return None
    if rerun.outcome is not RerunOutcome.ACCEPTED:
        repo.run.say(f"{repo.repo}'s re-run of {check.name} was {rerun.outcome}: {rerun.detail}")
        return rerun.outcome
    try:
        await repo.run.post(rerun_marker(repo.repo, check.name, head_sha), head_sha)
    except NotRecorded as exc:
        repo.run.say(f"re-run signature marker write failed (non-fatal): {exc}")
    return RerunOutcome.ACCEPTED


async def failed_checks(
    repo: RepoLanding, head: str, verdict: Verdict, checks: list[dict[str, Any]], identity: dict[str, Any]
) -> RepoState:
    """Route owned failures and rerun inherited failures once, independent of how the revision lands. An inherited
    failure waits for every check on the head to conclude before it is re-run or charged."""
    run = repo.run
    if not all(inherited(check["base_red"]) for check in checks):
        run.say(f"{repo.repo} has a terminal CI check failure — will not re-poll")
        return Failure(({**identity, "decision": FAILED, "checks": checks},))
    if verdict.pending:
        run.say(f"{repo.repo}'s failing checks are inherited from the base — a check is still running; re-polling")
        return Waiting(f"{repo.repo}'s inherited failures wait for the run to finish")
    failing = verdict.failing
    unsigned = [
        check
        for check in failing
        if Capability.CHECKS_RERUN in repo.forge.offered
        and rerun_marker(repo.repo, check.name, head) not in run.visit.marker_names()
    ]
    if unsigned:
        outcomes = [await rerun_once(repo, check, head) for check in unsigned]
        if RerunOutcome.REFUSED in outcomes:
            run.say(f"{repo.repo}'s failing checks could not be re-run — will not re-poll")
            return Failure(({**identity, "decision": FAILED, "checks": checks},))
        if RerunOutcome.RUN_IN_PROGRESS in outcomes:
            run.say(f"{repo.repo}'s run is still going — the re-run waits; re-polling")
            return Waiting(f"{repo.repo}'s inherited failures wait for the run to finish")
        run.say(f"{repo.repo}'s failing checks are inherited from the base — re-requested once; re-polling")
        return Waiting(f"{repo.repo}'s inherited failures re-run")
    rows = [
        {
            "name": check.name,
            "conclusion": check.detail,
            "details_url": check.details_url,
            "signature": rerun_marker(repo.repo, check.name, head),
        }
        for check in failing
    ]
    run.say(f"{repo.repo}'s failing checks are still inherited from the base after a re-run — repair the base")
    return Inherited(({**identity, "decision": INHERITED_FAILURE, "base_branch": repo.base_branch, "checks": rows},))


@domain_model
@dataclass(frozen=True)
class Findings:
    """The ``delivery-findings`` marker body — plain markdown a resolve worker reads, not JSON. Each record
    carries ``repo``/``number``/``url``/``decision``, and what its decision names."""

    records: tuple[dict[str, Any], ...]

    def render(self) -> str:
        return "\n\n".join(_Section.of(record).render() for record in self.records)


@domain_model
@dataclass(frozen=True)
class _Section:
    """One repo's section: a header, a label, and one line per check."""

    record: dict[str, Any]

    label: ClassVar[str] = ""

    @classmethod
    def of(cls, record: dict[str, Any]) -> _Section:
        if record["decision"] == FAILED:
            return _Failed(record)
        if record["decision"] == INHERITED_FAILURE:
            return _InheritedFailed(record)
        if record["decision"] == FOREIGN:
            return _ForeignHead(record)
        return _Running(record)

    def rows(self) -> Iterator[str]:
        raise NotImplementedError

    def render(self) -> str:
        header = f"## {self.record['repo']}#{self.record['number']} — {self.record['url']}"
        return "\n".join([header, self.label, *self.rows()])


class _Failed(_Section):
    label = "CI check failures:"

    def rows(self) -> Iterator[str]:
        for check in self.record["checks"]:
            yield f"  - {check['name']}: {check['conclusion']} — {check['details_url']}"
            base_red = check.get("base_red")
            if base_red is True:
                yield f"    base branch: also failing {check['name']} — not this change"
            elif base_red is False:
                yield f"    base branch: {check['name']} is clean — this change broke CI"


class _InheritedFailed(_Section):
    label = "Inherited from the base — re-run once, still failing (not this change):"

    def rows(self) -> Iterator[str]:
        for check in self.record["checks"]:
            yield f"  - {check['name']}: {check['conclusion']} — {check['details_url']}"
            yield f"    signature: {check['signature']}"
        yield f"  base branch: {self.record['base_branch']}"


class _ForeignHead(_Section):
    label = "The PR head carries more than the submitted commit (a foreign advance, not a CI failure):"

    def rows(self) -> Iterator[str]:
        yield f"  submitted commit: {self.record['submitted']}"
        yield f"  live head: {self.record['head']}"
        for offender in self.record["offenders"]:
            yield f"  - {offender}"


class _Running(_Section):
    label = "Still running:"

    def rows(self) -> Iterator[str]:
        for check in self.record["checks"]:
            yield f"  - {check['name']}: {check['status']}"
