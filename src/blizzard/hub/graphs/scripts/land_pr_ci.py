"""The PR + CI-watch delivery policy's `deliver` node script — self-healing. Routes by the
PR's live ``mergeable_state`` plus, for a ``clean``/``blocked``/``unstable`` head, its check
runs — what eligibility turns on. ``behind`` self-heals via ``update-branch``, ``dirty`` is
the one true LLM kick-back, everything else waits. Merges via merge commit. Honors the
hub-command-node authoring contract (``blizzard-context:/standards/hub-nodes.md``).

The submitted commit stays authoritative: before any routing, every repo's live PR head must
be that commit or descend from it through nothing but merges of the base branch (first-parent
chain, each merge contributing exactly the base side's own change). Any other commit on the
head prints ``failure`` with ``delivery-findings`` and ``delivery-findings/foreign-head`` markers
naming it, and nothing is updated or merged for any repo; an unprovable head refuses, an
unreadable one waits. The verified head sha is the one the check read, ``update-branch`` and the
merge all carry."""

from __future__ import annotations

import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, ClassVar

from blizzard.hub.graphs.scripts.land_common import (
    ForgeReadDegraded,
    LandedRevisionUnknown,
    LandRun,
    MarkerWriteError,
    MergeDidNotLand,
    NothingToLand,
    PullRequest,
    PullRequestLookupError,
    PullRequestOpenError,
)

# The reserved + authored outcomes this script prints as its last stdout line.
_LANDED = "landed"
_CONFLICT = "conflict"
_PENDING = "pending"
# The graph's authored `failure` choice — printed as an outcome, unlike
# `_FAILED` below, which is an internal per-repo decision.
_CI_FAILURE = "failure"
# The graph's authored `inherited-failure` choice: every remaining check failure is
# inherited from the base branch and survived a one-time re-run — not this chunk's
# defect, so it routes straight to a repair node rather than through `resolve`.
_INHERITED_FAILURE = "inherited-failure"

# The marker name a terminal-CI-failure or a substantive wait writes its findings under.
_FINDINGS_NAME = "delivery-findings"
# A foreign-advance refusal also writes here: the hub keeps only the first write per name in a
# visit, so an earlier wait's `delivery-findings` would otherwise hide the refusal from the
# repair worker.
_FOREIGN_FINDINGS_NAME = f"{_FINDINGS_NAME}/foreign-head"

# The re-run signature marker's name prefix: one per (repo, check name, head sha) a
# base-inherited failure was re-requested under, so a re-entry to `deliver` can tell
# "already re-run once at this head" from "seeing this for the first time" without any
# state but the marker names `BZ_HUB_ARTIFACT_NAMES` carries in.
_RERUN_MARKER_PREFIX = "ci-rerun/"

# Pure routing decisions (what to do with one repo after reading its live PR).
_PUSH = "push"  # clean (or already merged) — eligible for the merge stage
_WAIT = "wait"  # unknown / required-checks-not-green / … — re-poll, no side effect
_UPDATE = "update"  # behind — fire update-branch, then re-poll
_BOUNCE = "bounce"  # dirty — a real content conflict, kick back to build
_FAILED = "failed"  # a check run completed with a terminal conclusion — never re-poll
_FOREIGN = "foreign-head"  # the PR head carries something beyond the submitted commit and base merges

# A compare lists at most this many files; a list that long may be cut short, so it proves nothing.
_COMPARE_FILES_MAX = 300

# A completed check run in any of these is never going to turn green on its own, so
# polling on out to `poll_timeout` only burns the slot. `cancelled` is NOT
# one of these: a concurrency-group cancellation is not a failed job, and a check run's
# payload alone cannot tell the two apart, so it is re-polled instead — bounded, same as
# any other non-terminal status, by `poll_timeout`.
_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "action_required"}

# The conclusions a green check run may carry — required or unrequired alike.
_GREEN_CONCLUSIONS = {"success", "neutral", "skipped"}

# States whose merge eligibility needs a live check-run verdict, not just `mergeable_state`.
_CI_WATCH_STATES = {"clean", "blocked", "unstable"}

# Whether a head-failing check is inherited from the base, or the chunk's own. Kept off
# `Verdict` itself: the classification is a pure function of TWO readings — the head's own
# terminal conclusion (already established) and the base's reading of that same check
# name — not a property either reading carries alone.
_OWN = "own"
_INHERITED = "inherited"


@dataclass(frozen=True)
class Route:
    """Where one repo goes, from its PR's live ``(merged, mergeable_state, verdict)`` —
    pure. Already-merged and ``dirty``/``behind`` never need a verdict; a
    ``clean``/``blocked``/``unstable`` head pushes only on a green one, else waits."""

    mergeable_state: str | None
    merged: bool = False
    verdict: Verdict | None = None

    @classmethod
    def of(cls, pull: PullRequest, verdict: Verdict | None = None) -> Route:
        return cls(pull.mergeable_state, merged=pull.merged, verdict=verdict)

    @property
    def decision(self) -> str:  # ast-grep-ignore: bzh:property-delegates
        if self.merged:
            return _PUSH
        if self.mergeable_state == "dirty":
            return _BOUNCE
        if self.mergeable_state == "behind":
            return _UPDATE
        if self.mergeable_state in _CI_WATCH_STATES:
            return _PUSH if self.verdict is not None and self.verdict.green else _WAIT
        return _WAIT


@dataclass(frozen=True)
class Verdict:
    """One ref's live check runs, classified. ``None`` is a degraded read —
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
        required or not, as the forge would have it."""
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
        return _FAILED if self.failing else _WAIT

    @property
    def substantive(self) -> bool:
        """Whether this read says anything: a zero-check read is not worth findings."""
        return bool(self.check_runs)

    @property
    def green(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        """The merge-eligibility bar: at least one check run, and every one of them
        completed with a conclusion in `_GREEN_CONCLUSIONS` — a degraded or empty read is
        never green."""
        runs = self.check_runs if isinstance(self.check_runs, list) else []
        if not runs:
            return False
        return all(
            isinstance(run, dict) and run.get("status") == "completed" and run.get("conclusion") in _GREEN_CONCLUSIONS
            for run in runs
        )

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
class HeadGate:
    """Whether one repo's live PR head is still the submitted work. ``offenders`` names what
    broke the rule — a commit sha, or the reason the head could not be proven."""

    offenders: list[str]

    @property
    def admitted(self) -> bool:
        return not self.offenders


def _first_parent_chain(commits: list[Any], head: str, submitted: str) -> tuple[list[dict[str, Any]], str | None]:
    """The commits on ``head``'s first-parent path back to ``submitted``, newest first, and
    the reason the path cannot be walked — ``None`` when it reaches ``submitted``. Pure."""
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


def _changed_lines(file: dict[str, Any]) -> Counter[str] | None:
    """The multiset of added and removed lines in one compare file's patch, or ``None`` when
    the forge sent no patch to read. Pure."""
    patch = file.get("patch")
    if not isinstance(patch, str):
        return None
    return Counter(line for line in patch.splitlines() if line[:1] in {"+", "-"})


def _files_by_name(payload: dict[str, Any]) -> dict[str, dict[str, Any]] | None:
    """A compare's files keyed by name, or ``None`` when the list may be truncated or is
    malformed — a list that cannot be trusted whole proves nothing. Pure."""
    files = payload.get("files")
    if not isinstance(files, list) or len(files) >= _COMPARE_FILES_MAX:
        return None
    if not all(isinstance(f, dict) and isinstance(f.get("filename"), str) for f in files):
        return None
    return {f["filename"]: f for f in files}


def _contribution_matches(merged: dict[str, Any], base_side: dict[str, Any]) -> bool:
    """Whether a merge's contribution onto its first parent equals the base side's own
    change: the same files, each with the same resulting blob, or — where the feature
    changed the file too — the same added and removed lines. Pure; unprovable is ``False``."""
    got, want = _files_by_name(merged), _files_by_name(base_side)
    if got is None or want is None or got.keys() != want.keys():
        return False
    for name, file in got.items():
        other = want[name]
        if file.get("previous_filename") != other.get("previous_filename"):
            return False
        if file.get("sha") is not None and file.get("sha") == other.get("sha"):
            continue
        lines, other_lines = _changed_lines(file), _changed_lines(other)
        if lines is None or other_lines is None or lines != other_lines:
            return False
    return True


def gate_head(run: LandRun, bare_repo: str, submitted: str, head: str) -> HeadGate:
    """Whether ``head`` is ``submitted`` or descends from it through base merges alone.

    Reads only; raises :class:`ForgeReadDegraded` on a read that got no answer. An answered
    response this cannot prove refuses — a false refusal costs one repair visit, a false
    accept lands unverified work."""
    if head == submitted:
        return HeadGate([])
    ahead = run.compare(bare_repo, submitted, head)
    if ahead is None:
        return HeadGate([f"the forge would not compare {submitted}...{head}"])
    if ahead.get("status") == "identical":
        return HeadGate([])
    if ahead.get("status") != "ahead":
        return HeadGate([f"{head} does not descend from the submitted commit {submitted} (it was rewritten or reset)"])
    commits = ahead.get("commits")
    total = ahead.get("total_commits", ahead.get("ahead_by"))
    if not isinstance(commits, list) or not isinstance(total, int) or len(commits) != total:
        return HeadGate([f"the commit list between {submitted} and {head} is truncated or unreadable"])
    chain, unreachable = _first_parent_chain(commits, head, submitted)
    if unreachable is not None:
        return HeadGate([unreachable])
    offenders: list[str] = []
    for commit in chain:
        sha = commit["sha"]
        parents = [p["sha"] for p in commit["parents"] if isinstance(p, dict) and isinstance(p.get("sha"), str)]
        if len(parents) != 2 or len(commit["parents"]) != 2:
            offenders.append(f"{sha} (not a merge of the base branch)")
            continue
        first, second = parents
        if not run.base_holds(bare_repo, second):
            offenders.append(f"{sha} (merges {second}, which the base branch does not hold)")
            continue
        merged = run.compare(bare_repo, first, sha)
        base_side = run.compare(bare_repo, first, second)
        if merged is None or base_side is None:
            offenders.append(f"{sha} (its content could not be compared with the base branch's change)")
        elif not _contribution_matches(merged, base_side):
            offenders.append(f"{sha} (adds content beyond the base branch's change)")
    return HeadGate(offenders)


def _inheritance(base_red: bool | None) -> str:
    """Whether a head-failing check is inherited from the base, given the base's own
    reading of that same check name. A degraded base read (``None``) is conservative:
    unknown counts as base-green, so the failure is charged to this chunk exactly like a
    clean base-green read, never silently waived."""
    return _INHERITED if base_red is True else _OWN


def _rerun_marker(repo: str, name: str, head_sha: str) -> str:
    """The re-run signature marker name for one (repo, check name, head sha) triple."""
    return f"{_RERUN_MARKER_PREFIX}{repo}/{name}/{head_sha}"


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
        if record["decision"] == _FAILED:
            return _Failed(record)
        if record["decision"] == _INHERITED_FAILURE:
            return _InheritedFailed(record)
        if record["decision"] == _FOREIGN:
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


class _Conflict(Exception):
    """Raised to abort the check stage as a real conflict — nothing has been merged."""


def _rerequest_once(run: LandRun, repo: str, check: dict[str, Any], head_sha: str) -> None:
    """Fire GitHub's check-run rerequest route once for ``check``, then record its
    signature marker — side effect first, exactly like
    :meth:`land_common.MarkerWriter.record`: a crash before the marker is durable just
    re-fires the rerequest on the next poll, which is harmless to repeat. Neither the
    forge call nor the marker write is fatal to the run: a re-request that never fires
    just leaves the same failure to be re-read, and re-attempted, on the next poll. The
    marker is written only when the forge answers 2xx — a refusal must not read as a re-run."""
    check_id = check.get("id")
    if check_id is None:
        return
    try:
        status, _ = run.api("POST", f"/repos/{repo}/check-runs/{check_id}/rerequest")
    except Exception:
        return
    if not 200 <= status < 300:
        return
    try:
        run.markers.post(_rerun_marker(repo, check.get("name", ""), head_sha), head_sha)
    except MarkerWriteError as exc:
        print(f"re-run signature marker write failed (non-fatal): {exc}", file=sys.stderr)


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

    wait = False
    # --- gate stage: every repo's live head is proven to still be the submitted work BEFORE
    #     any repo is routed, so a foreign head in one repo fires no update-branch and no
    #     merge in any other.
    resolved: list[tuple[dict[str, str], PullRequest]] = []
    foreign: list[dict[str, Any]] = []
    for commit in pending:
        try:
            pull = PullRequest.of(run, commit)
        except NothingToLand as exc:
            # A no-op landing: nothing to merge, and no poll changes that. The marker
            # stops this repo being pending and completes the chunk's repo set.
            print(f"{exc} — nothing to land", file=sys.stderr)
            run.markers.record(commit["repo"], exc.landed_sha)
            continue
        except LandedRevisionUnknown as exc:
            # The no-op is confirmed but its landed revision unread — another poll rereads it.
            print(str(exc), file=sys.stderr)
            wait = True
            continue
        except PullRequestOpenError as exc:
            # A create hiccup is worth another poll, not a bounce.
            print(str(exc), file=sys.stderr)
            wait = True
            continue
        except PullRequestLookupError as exc:
            # A degraded read is worth another poll too — never treated as "not merged".
            print(str(exc), file=sys.stderr)
            wait = True
            continue
        if pull.head_sha and not pull.merged:
            try:
                gate = gate_head(run, commit["repo"], commit["commit"], pull.head_sha)
            except ForgeReadDegraded as exc:
                print(f"{pull}: {exc}; re-polling", file=sys.stderr)
                wait = True
                continue
            if not gate.admitted:
                print(f"{pull}'s head {pull.head_sha} is not the submitted commit plus base merges", file=sys.stderr)
                foreign.append(
                    {
                        "repo": pull.repo,
                        "number": pull.number,
                        "url": pull.url,
                        "decision": _FOREIGN,
                        "submitted": commit["commit"],
                        "head": pull.head_sha,
                        "offenders": gate.offenders,
                    }
                )
                continue
        resolved.append((commit, pull))

    if foreign:
        # Nothing is updated or merged for any repo. The write is unguarded: unwritten
        # findings leave the repair worker nothing to read.
        rendered = Findings(foreign).render()
        run.markers.post(_FINDINGS_NAME, rendered)
        run.markers.post(_FOREIGN_FINDINGS_NAME, rendered)
        print(_CI_FAILURE)
        return 0

    # --- check stage: no repo is merged unless ALL check `clean` (chunk atomicity), and
    #     the loop never short-circuits on a failure, so findings accumulate together.
    to_merge: list[tuple[PullRequest, str]] = []
    failures: list[dict[str, Any]] = []
    inherited: list[dict[str, Any]] = []
    wait_records: list[dict[str, Any]] = []
    try:
        for commit, pull in resolved:
            head_sha = pull.head_sha or commit["commit"]
            state = pull.mergeable_state
            # A verdict is only ever worth reading for the states merge eligibility itself
            # turns on — fetched once here and reused below, never re-read.
            verdict = Verdict.of(run, pull.repo, head_sha) if state in _CI_WATCH_STATES else None
            decision = Route.of(pull, verdict).decision
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
                if verdict is not None:
                    # A degraded CI read falls through to the plain wait below.
                    if verdict.decision == _FAILED:
                        base = Verdict.of(run, pull.repo, run.base_branch)
                        checks = verdict.failure_rows(base)
                        if any(_inheritance(check["base_red"]) == _OWN for check in checks):
                            # Any owned failure charges this chunk; inherited failures on
                            # the same repo accompany it in one `resolve` diagnosis.
                            failures.append(
                                {
                                    "repo": pull.repo,
                                    "number": pull.number,
                                    "url": pull.url,
                                    "decision": _FAILED,
                                    "checks": checks,
                                }
                            )
                            print(f"{pull} has a terminal CI check failure — will not re-poll", file=sys.stderr)
                            continue
                        # Every failing check is inherited from the base. Re-run each once
                        # before charging a repair: one not yet re-requested at this head
                        # sha fires the re-run and waits; one already re-requested and
                        # still red is confirmed inherited.
                        unsigned = [
                            check
                            for check in verdict.failing
                            if _rerun_marker(pull.repo, check.get("name", ""), head_sha) not in run.already
                        ]
                        if unsigned:
                            for check in unsigned:
                                _rerequest_once(run, pull.repo, check, head_sha)
                            print(
                                f"{pull}'s failing checks are inherited from the base — re-requested once; re-polling",
                                file=sys.stderr,
                            )
                            wait = True
                            continue
                        inherited.append(
                            {
                                "repo": pull.repo,
                                "number": pull.number,
                                "url": pull.url,
                                "decision": _INHERITED_FAILURE,
                                "base_branch": run.base_branch,
                                "checks": [
                                    {
                                        "name": check.get("name"),
                                        "conclusion": check.get("conclusion"),
                                        "details_url": check.get("details_url"),
                                        "signature": _rerun_marker(pull.repo, check.get("name", ""), head_sha),
                                    }
                                    for check in verdict.failing
                                ],
                            }
                        )
                        print(
                            f"{pull}'s failing checks are still inherited from the base after a re-run "
                            "— not this chunk's defect; routing to repair the base",
                            file=sys.stderr,
                        )
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
            # decision == _PUSH: already merged, or a green verdict at a clean/blocked/
            # unstable head — eligible.
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

    if inherited:
        # Nothing merges (chunk atomicity), same as `failures` above: unwritten findings
        # leave the repair charge nothing to read, so this write is unguarded too.
        run.markers.post(_FINDINGS_NAME, Findings(inherited).render())
        print(_INHERITED_FAILURE)
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
    pending_count = len(to_merge)
    for marker_index, (pull, head_sha) in enumerate(to_merge, start=1):
        try:
            landed_sha = pull.merge(head_sha, method="merge")
        except MergeDidNotLand as exc:
            # Not an already-merged prior run (`merge` absorbs that) — a race worth re-polling.
            print(f"merge of {pull} did not land ({exc.result}); will re-poll", file=sys.stderr)
            print(_PENDING)
            return 0
        except LandedRevisionUnknown as exc:
            # Merged, but no read named its commit — the next poll recovers it through the
            # already-merged path.
            print(f"{exc}; will re-poll", file=sys.stderr)
            print(_PENDING)
            return 0
        run.markers.record(pull.bare_repo, landed_sha)
        run.pause_for_crash_window(marker_index=marker_index, pending_count=pending_count)

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


#: The only shape that turns a `clean`/`blocked`/`unstable` head into a push.
_GREEN_VERDICT = Verdict([{"status": "completed", "conclusion": "success"}])
#: A check run still in flight — `clean` alone is never enough to push.
_PENDING_VERDICT = Verdict([{"status": "in_progress", "conclusion": None}])


class _RouteTable(_Table):
    label = "routing cases"
    cases: ClassVar[list[tuple[Any, str]]] = [
        (("clean", False, _GREEN_VERDICT), _PUSH),
        ((None, True, None), _PUSH),  # already merged (interrupted prior run) — no verdict read
        (("clean", True, None), _PUSH),  # merged wins outright, whatever the verdict
        (("dirty", False, None), _BOUNCE),  # the ONLY true LLM bounce
        (("behind", False, None), _UPDATE),  # self-heal, no LLM
        (("unknown", False, None), _WAIT),  # transient — GitHub still computing
        (("clean", False, _PENDING_VERDICT), _WAIT),  # merge refused while checks are pending
        (("clean", False, None), _WAIT),  # clean alone, no verdict read — never push blind
        (("blocked", False, _PENDING_VERDICT), _WAIT),  # required CI/reviews not green — CI-watch
        (("unstable", False, _PENDING_VERDICT), _WAIT),
        (("blocked", False, _GREEN_VERDICT), _PUSH),  # branch protection lagging a green head
        (("has_hooks", False, None), _WAIT),
        (("draft", False, None), _WAIT),
        ((None, False, None), _WAIT),  # missing state — wait, never bounce
    ]

    def subject(self, case: Any) -> str:
        state, merged, verdict = case
        return f"({state!r}, merged={merged}, green={verdict.green if verdict else None})"

    def decide(self, case: Any) -> str:
        state, merged, verdict = case
        return Route(state, merged=merged, verdict=verdict).decision


class _CheckTable(_Table):
    label = "check-run cases"
    cases: ClassVar[list[tuple[Any, str]]] = [
        ([{"status": "completed", "conclusion": "failure"}], _FAILED),
        ([{"status": "completed", "conclusion": "timed_out"}], _FAILED),
        ([{"status": "completed", "conclusion": "action_required"}], _FAILED),
        # `cancelled` is NOT terminal: a concurrency-group cancellation is not a failed
        # job, and the payload alone can't tell the two apart, so it waits.
        ([{"status": "completed", "conclusion": "cancelled"}], _WAIT),
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


class _InheritanceTable(_Table):
    """Whether a head-failing check is inherited from the base — pure, given only the
    base's own reading of that check name."""

    label = "inheritance cases"
    cases: ClassVar[list[tuple[Any, str]]] = [
        (True, _INHERITED),  # head red, base red on the same check — inherited
        (False, _OWN),  # head red, base green — this chunk's own failure
        (None, _OWN),  # head red, base unreadable — conservative: charged, not waived
    ]

    def subject(self, case: Any) -> str:
        return f"base_red={case!r}"

    def decide(self, case: Any) -> str:
        return _inheritance(case)


def _selftest() -> int:
    """Assert the pure routing tables — no network. The classification is the risk."""
    tables: tuple[_Table, ...] = (_RouteTable(), _CheckTable(), _InheritanceTable())
    return 1 if sum(table.run() for table in tables) else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
