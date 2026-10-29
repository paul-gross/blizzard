"""What ``land`` decides from what the forge said, with no forge call of its own: where one repository's
change routes, how its checks classify, whether its head is still the submitted work, and the
``delivery-findings`` a resolve worker reads."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from blizzard.foundation.roles import domain_model
from blizzard.hub.forge.landing import Check, CheckOutcome, CommitSummary, Mergeability

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


@domain_model
@dataclass(frozen=True)
class HeadGate:
    """Whether a live change head is still the submitted work; ``offenders`` names what broke it."""

    offenders: tuple[str, ...]

    def admitted(self) -> bool:
        return not self.offenders


def first_parent_chain(
    commits: tuple[CommitSummary, ...], head: str, submitted: str
) -> tuple[list[CommitSummary], str | None]:
    """``head``'s first-parent commits back to ``submitted``, and why the walk failed, if it did."""
    by_revision = {c.revision: c for c in commits}
    chain: list[CommitSummary] = []
    current = head
    while current != submitted:
        commit = by_revision.get(current)
        if commit is None or not commit.parents:
            return chain, f"{current} is not reachable from {submitted} within the listed commits"
        chain.append(commit)
        if len(chain) > len(by_revision):
            return chain, f"{current} has no readable first parent"
        current = commit.parents[0]
    return chain, None


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
