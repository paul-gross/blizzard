"""The PR + CI-watch delivery policy's `deliver` node script — self-healing.

Opens a PR per repo and routes by the PR's live ``mergeable_state``, resolving what is
mechanical or transient without waking the LLM: ``clean`` merges, ``behind`` self-heals via
``update-branch``, ``dirty`` is the one true LLM kick-back, everything else waits. Honors the
hub-command-node authoring contract (``blizzard-context:/standards/hub-nodes.md``)."""

from __future__ import annotations

import sys
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from blizzard.hub.graphs.scripts.land_common import (
    LandRun,
    MarkerWriteError,
    MergeDidNotLand,
    PullRequest,
    PullRequestOpenError,
)

# The reserved + authored outcomes this script prints as its last stdout line.
_LANDED = "landed"
_CONFLICT = "conflict"
_PENDING = "pending"
# The graph's authored `failure` choice (issue #232) — printed as an outcome, unlike
# `_FAILED` below, which is an internal per-repo decision.
_CI_FAILURE = "failure"

# The marker name a terminal-CI-failure or a substantive wait writes its findings under.
_FINDINGS_NAME = "delivery-findings"

# Pure routing decisions (what to do with one repo after reading its live PR).
_PUSH = "push"  # clean (or already merged) — eligible for the merge stage
_WAIT = "wait"  # unknown / required-checks-not-green / … — re-poll, no side effect
_UPDATE = "update"  # behind — fire update-branch, then re-poll
_BOUNCE = "bounce"  # dirty — a real content conflict, kick back to build
_FAILED = "failed"  # a check run completed with a terminal conclusion — never re-poll

# A completed check run in any of these is never going to turn green on its own, so
# polling on out to `poll_timeout` only burns the slot (issue #232).
_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}


@dataclass(frozen=True)
class Route:
    """Where one repo goes, from its PR's live ``(merged, mergeable_state)`` — pure.

    ``clean``/already-merged pushes, ``dirty`` bounces, ``behind`` self-heals via
    update-branch, and every other state waits, since none is a content conflict."""

    mergeable_state: str | None
    merged: bool = False

    @classmethod
    def of(cls, pull: PullRequest) -> Route:
        return cls(pull.mergeable_state, merged=pull.merged)

    @property
    def decision(self) -> str:
        if self.merged:
            return _PUSH
        if self.mergeable_state == "clean":
            return _PUSH
        if self.mergeable_state == "dirty":
            return _BOUNCE
        if self.mergeable_state == "behind":
            return _UPDATE
        return _WAIT


@dataclass(frozen=True)
class Verdict:
    """One ref's live check runs, classified (issue #232). ``None`` is a degraded read —
    it waits, exactly like a still-running one, and every malformed shape does too."""

    check_runs: list[dict[str, Any]] | None

    @classmethod
    def of(cls, run: LandRun, repo: str, ref: str) -> Verdict:
        """GET ``repo``'s check runs at ``ref``, degrading to ``None`` on ANY read failure —
        a non-200, a malformed body, or an outright exception (a real forge outage)."""
        try:
            status, payload = run.api("GET", f"/repos/{repo}/commits/{ref}/check-runs")
        except Exception:
            return cls(None)
        if status != 200 or not isinstance(payload, dict):
            return cls(None)
        check_runs = payload.get("check_runs")
        return cls(check_runs if isinstance(check_runs, list) else None)

    @staticmethod
    def terminal(run: Any) -> bool:
        """Whether one run has completed in a conclusion no re-poll will turn green —
        required or not, as the forge would have it (issue #232)."""
        return (
            isinstance(run, dict)
            and run.get("status") == "completed"
            and run.get("conclusion") in _TERMINAL_CONCLUSIONS
        )

    @property
    def failing(self) -> list[dict[str, Any]]:
        runs = self.check_runs if isinstance(self.check_runs, list) else []
        return [run for run in runs if self.terminal(run)]

    @property
    def decision(self) -> str:
        return _FAILED if self.failing else _WAIT

    @property
    def substantive(self) -> bool:
        """Whether this read says anything: a zero-check read is not worth findings."""
        return bool(self.check_runs)

    def red(self, name: str) -> bool | None:
        """Whether this ref's own latest run named ``name`` is itself terminal — ``None``
        on a degraded read, ``False`` when read but carrying no terminal run of that name."""
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


@dataclass(frozen=True)
class Findings:
    """The ``delivery-findings`` marker body — plain markdown a resolve worker reads, not
    JSON. Each record carries ``repo``/``number``/``url``/``decision``/``checks``."""

    records: list[dict[str, Any]]

    def render(self) -> str:
        return "\n\n".join(_Section.of(record).render() for record in self.records)


@dataclass(frozen=True)
class _Section:
    """One repo's section: a header, a label, and one line per check."""

    record: dict[str, Any]

    label: ClassVar[str] = ""

    @classmethod
    def of(cls, record: dict[str, Any]) -> _Section:
        return (_Failed if record["decision"] == _FAILED else _Running)(record)

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


class _Running(_Section):
    label = "Still running:"

    def rows(self) -> Iterator[str]:
        for check in self.record["checks"]:
            yield f"  - {check['name']}: {check['status']}"


class _Conflict(Exception):
    """Raised to abort the check stage as a real conflict — nothing has been merged."""


def main() -> int:
    """Run the land policy, aborting cleanly on an unconfirmed marker write."""
    try:
        return _land()
    except MarkerWriteError as exc:
        print(f"marker write failed: {exc}", file=sys.stderr)
        return 1


def _land() -> int:
    run = LandRun.from_env()
    pending = run.pending()
    if not pending:
        print(_LANDED)
        return 0

    # --- check stage: no repo is merged unless ALL check `clean` (chunk atomicity), and
    #     the loop never short-circuits on a failure, so findings accumulate together.
    to_merge: list[tuple[PullRequest, str]] = []
    failures: list[dict[str, Any]] = []
    wait_records: list[dict[str, Any]] = []
    wait = False
    try:
        for commit in pending:
            try:
                pull = PullRequest.of(run, commit)
            except PullRequestOpenError as exc:
                # A create hiccup is worth another poll, not a bounce.
                print(str(exc), file=sys.stderr)
                wait = True
                continue
            head_sha = pull.head_sha or commit["commit"]
            state = pull.mergeable_state
            decision = Route.of(pull).decision
            if decision == _BOUNCE:
                raise _Conflict(f"{pull} is dirty (a real merge conflict)")
            if decision == _UPDATE:
                # Any non-202 other than a named conflict waits — the NEXT poll's state
                # is the authoritative one.
                ustatus, message = pull.update_branch(head_sha)
                if ustatus == 422 and "conflict" in message.lower():
                    raise _Conflict(f"{pull} update-branch reported a conflict: {message}")
                print(f"{pull} behind — update-branch requested (HTTP {ustatus}); re-polling", file=sys.stderr)
                wait = True
                continue
            if decision == _WAIT:
                if state in {"blocked", "unstable"}:
                    # The CI-watch case (issue #232): a degraded read falls through to the
                    # plain wait below.
                    verdict = Verdict.of(run, pull.repo, head_sha)
                    if verdict.decision == _FAILED:
                        base = Verdict.of(run, pull.repo, run.base_branch)
                        failures.append(
                            {
                                "repo": pull.repo,
                                "number": pull.number,
                                "url": pull.url,
                                "decision": _FAILED,
                                "checks": verdict.failure_rows(base),
                            }
                        )
                        print(f"{pull} has a terminal CI check failure — will not re-poll", file=sys.stderr)
                        continue
                    if verdict.substantive:
                        wait_records.append(
                            {
                                "repo": pull.repo,
                                "number": pull.number,
                                "url": pull.url,
                                "decision": _WAIT,
                                "checks": verdict.running_rows(),
                            }
                        )
                print(f"{pull} is {state} — not cleanly mergeable yet; re-polling", file=sys.stderr)
                wait = True
                continue
            # decision == _PUSH: clean (or already merged) — eligible.
            to_merge.append((pull, head_sha))
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print(_CONFLICT)
        return 0

    if failures:
        # Nothing merges (chunk atomicity). The write is deliberately unguarded, unlike
        # the wait path below: unwritten findings leave `resolve` nothing to read (#243).
        run.markers.post(_FINDINGS_NAME, Findings(failures).render())
        print(_CI_FAILURE)
        return 0

    if wait:
        # Merge NOTHING (chunk atomicity). Findings are re-written every poll, and a write
        # failure here degrades to `pending` since the next poll re-writes them (#243).
        if wait_records:
            try:
                run.markers.post(_FINDINGS_NAME, Findings(wait_records).render())
            except MarkerWriteError as exc:
                print(f"delivery-findings write failed (wait path, non-fatal): {exc}", file=sys.stderr)
        print(_PENDING)
        return 0

    # --- merge stage: merge the CURRENT head sha, which a self-heal update-branch may
    #     have advanced past the originally-recorded artifact commit.
    for pull, head_sha in to_merge:
        try:
            landed_sha = pull.merge(head_sha)
        except MergeDidNotLand as exc:
            # Not an already-merged prior run (`merge` absorbs that) — a race worth re-polling.
            print(f"merge of {pull} did not land ({exc.result}); will re-poll", file=sys.stderr)
            print(_PENDING)
            return 0
        run.markers.record(pull.bare_repo, landed_sha)

    print(_LANDED)
    return 0


@dataclass(frozen=True)
class _Table:
    """One selftest table: its cases printed a line each, then a PASS/FAIL tally."""

    cases: ClassVar[list[tuple[Any, str]]] = []
    label: ClassVar[str] = ""

    def subject(self, case: Any) -> str:
        raise NotImplementedError

    def decide(self, case: Any) -> str:
        raise NotImplementedError

    def run(self) -> int:
        failures = 0
        for case, expected in self.cases:
            got = self.decide(case)
            ok = got == expected
            failures += not ok
            print(f"  {'ok ' if ok else 'FAIL'}  {self.subject(case)} -> {got}  (want {expected})")
        print(f"{'PASS' if not failures else 'FAIL'}: {len(self.cases) - failures}/{len(self.cases)} {self.label}")
        return failures


class _RouteTable(_Table):
    label = "routing cases"
    cases: ClassVar[list[tuple[Any, str]]] = [
        (("clean", False), _PUSH),
        ((None, True), _PUSH),  # already merged (interrupted prior run) — re-derive no-op
        (("clean", True), _PUSH),
        (("dirty", False), _BOUNCE),  # the ONLY true LLM bounce
        (("behind", False), _UPDATE),  # self-heal, no LLM
        (("unknown", False), _WAIT),  # transient — GitHub still computing
        (("blocked", False), _WAIT),  # required CI/reviews not green — the CI-watch wait
        (("unstable", False), _WAIT),
        (("has_hooks", False), _WAIT),
        (("draft", False), _WAIT),
        ((None, False), _WAIT),  # missing state — wait, never bounce
    ]

    def subject(self, case: Any) -> str:
        state, merged = case
        return f"({state!r}, merged={merged})"

    def decide(self, case: Any) -> str:
        state, merged = case
        return Route(state, merged=merged).decision


class _CheckTable(_Table):
    label = "check-run cases"
    cases: ClassVar[list[tuple[Any, str]]] = [
        ([{"status": "completed", "conclusion": "failure"}], _FAILED),
        ([{"status": "completed", "conclusion": "timed_out"}], _FAILED),
        ([{"status": "completed", "conclusion": "cancelled"}], _FAILED),
        ([{"status": "completed", "conclusion": "action_required"}], _FAILED),
        ([{"status": "queued", "conclusion": None}], _WAIT),
        ([{"status": "in_progress", "conclusion": None}], _WAIT),
        ([{"status": "waiting", "conclusion": None}], _WAIT),
        ([{"status": "requested", "conclusion": None}], _WAIT),
        ([], _WAIT),  # no check runs reported yet
        ([{"name": "build"}], _WAIT),  # malformed — missing status/conclusion, never raises
    ]

    def subject(self, case: Any) -> str:
        return f"{case!r}"

    def decide(self, case: Any) -> str:
        return Verdict(case).decision


def _selftest() -> int:
    """Assert the pure routing tables — no network. The classification is the risk."""
    return 1 if sum(table.run() for table in (_RouteTable(), _CheckTable())) else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
