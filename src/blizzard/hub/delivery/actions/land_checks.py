"""What ``land`` decides from what the forge said, with no forge call of its own: where one repository's
pull request routes, how its check runs classify, whether its head is still the submitted work, and the
``delivery-findings`` a resolve worker reads."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from blizzard.foundation.roles import domain_model

# Pure routing decisions (what to do with one repo after reading its live PR).
PUSH = "push"  # clean (or already merged) — eligible for the merge stage
WAIT = "wait"  # unknown / required-checks-not-green / … — re-poll, no side effect
UPDATE = "update"  # behind — fire update-branch, then re-poll
BOUNCE = "bounce"  # dirty — a real content conflict, kick back to build
FAILED = "failed"  # a check run completed with a terminal conclusion — never re-poll
FOREIGN = "foreign-head"  # the PR head carries something beyond the submitted commit and base merges
# A findings record's decision when every failing check is inherited — the answer's own name.
INHERITED_FAILURE = "inherited-failure"

#: Conclusions no re-poll turns green. Not `cancelled`: a concurrency-group cancellation is no failed job.
_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "action_required"}

# The conclusions a green check run may carry — required or unrequired alike.
_GREEN_CONCLUSIONS = {"success", "neutral", "skipped"}

#: States whose merge eligibility needs a live check-run verdict, not just `mergeable_state`.
CI_WATCH_STATES = {"clean", "blocked", "unstable"}


@domain_model
@dataclass(frozen=True)
class Verdict:
    """One ref's live check runs, classified. ``None`` is a degraded read — it waits, exactly like a
    still-running one, and every malformed shape does too."""

    check_runs: list[dict[str, Any]] | None

    @classmethod
    def of(cls, status: int, payload: Any) -> Verdict:
        """A check-runs read's verdict; a non-200 or a malformed body degrades to ``None``."""
        if status != 200 or not isinstance(payload, dict):
            return cls(None)
        check_runs = payload.get("check_runs")
        return cls(check_runs if isinstance(check_runs, list) else None)

    @staticmethod
    def terminal(run: Any) -> bool:
        """Whether one run has completed in a conclusion no re-poll will turn green — required or not, as
        the forge would have it."""
        return (
            isinstance(run, dict)
            and run.get("status") == "completed"
            and run.get("conclusion") in _TERMINAL_CONCLUSIONS
        )

    @property
    def failing(self) -> list[dict[str, Any]]:  # ast-grep-ignore: bzh:property-delegates
        runs = self.check_runs if isinstance(self.check_runs, list) else []
        return [run for run in runs if self.terminal(run)]

    @property
    def decision(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        return FAILED if self.failing else WAIT

    @property
    def substantive(self) -> bool:
        """Whether this read says anything: a zero-check read is not worth findings."""
        return bool(self.check_runs)

    @property
    def green(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """The merge-eligibility bar: at least one check run, and every one of them completed with a
        conclusion in `_GREEN_CONCLUSIONS` — a degraded or empty read is never green."""
        runs = self.check_runs if isinstance(self.check_runs, list) else []
        if not runs:
            return False
        return all(
            isinstance(run, dict) and run.get("status") == "completed" and run.get("conclusion") in _GREEN_CONCLUSIONS
            for run in runs
        )

    def red(self, name: str) -> bool | None:
        """Whether this ref's own latest run named ``name`` is itself terminal — ``None`` on a degraded
        read, ``False`` when read but carrying no terminal run of that name."""
        if self.check_runs is None:
            return None
        for run in self.check_runs:
            if isinstance(run, dict) and run.get("name") == name:
                return self.terminal(run)
        return False

    def failure_rows(self, base: Verdict) -> list[dict[str, Any]]:
        return [
            {
                "name": check.get("name"),
                "conclusion": check.get("conclusion"),
                "details_url": check.get("details_url"),
                "base_red": base.red(check.get("name", "")),
            }
            for check in self.failing
        ]

    def running_rows(self) -> list[dict[str, Any]]:
        return [
            {"name": check.get("name"), "status": check.get("status")}
            for check in (self.check_runs or [])
            if isinstance(check, dict)
        ]


@domain_model
@dataclass(frozen=True)
class Route:
    """Where one repo goes, from its PR's live ``(merged, mergeable_state, verdict)``. Already-merged and
    ``dirty``/``behind`` never need a verdict; a ``clean``/``blocked``/``unstable`` head pushes only on a
    green one, else waits."""

    mergeable_state: str | None
    merged: bool = False
    verdict: Verdict | None = None

    @property
    def decision(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if self.merged:
            return PUSH
        if self.mergeable_state == "dirty":
            return BOUNCE
        if self.mergeable_state == "behind":
            return UPDATE
        if self.mergeable_state in CI_WATCH_STATES:
            return PUSH if self.verdict is not None and self.verdict.green else WAIT
        return WAIT


def inherited(base_red: bool | None) -> bool:
    """Whether a head-failing check is inherited from the base, given the base's own reading of that same
    check name. A degraded base read (``None``) is conservative: unknown counts as base-green, so the
    failure is charged to this chunk exactly like a clean base-green read, never silently waived."""
    return base_red is True


@domain_model
@dataclass(frozen=True)
class HeadGate:
    """Whether a live PR head is still the submitted work; ``offenders`` names what broke it."""

    offenders: tuple[str, ...]

    def admitted(self) -> bool:
        return not self.offenders


def first_parent_chain(commits: list[Any], head: str, submitted: str) -> tuple[list[dict[str, Any]], str | None]:
    """``head``'s first-parent commits back to ``submitted``, and why the walk failed, if it did."""
    by_sha = {c["sha"]: c for c in commits if isinstance(c, dict) and isinstance(c.get("sha"), str)}
    chain: list[dict[str, Any]] = []
    current = head
    while current != submitted:
        commit = by_sha.get(current)
        parents = commit.get("parents") if commit else None
        if commit is None or not isinstance(parents, list) or not parents:
            return chain, f"{current} is not reachable from {submitted} within the listed commits"
        chain.append(commit)
        first = parents[0].get("sha") if isinstance(parents[0], dict) else None
        if not isinstance(first, str) or len(chain) > len(by_sha):
            return chain, f"{current} has no readable first parent"
        current = first
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
