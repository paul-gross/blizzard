"""The PR + CI-watch delivery policy's `deliver` node script — self-healing.

Opens a PR per repo and routes by the PR's live ``mergeable_state``, resolving what is
mechanical or transient without waking the LLM: ``clean`` merges, ``behind`` self-heals via
``update-branch``, ``dirty`` is the one true LLM kick-back, everything else waits. Honors the
hub-command-node authoring contract (``blizzard-context:/standards/hub-nodes.md``)."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from blizzard.hub.graphs.scripts.land_common import (
    _MARKER_PREFIX,
    MarkerWriteError,
    forge_request,
    marker_recorder,
    post_marker,
    pr_title,
    qualify_repo,
    refuse_empty_delivery,
    require_env,
    require_json_env,
)

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"
_ENV_FEATURE_TITLE = "BZ_HUB_FEATURE_TITLE"

_HUB_USER = "blizzard-hub"

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


def classify(mergeable_state: str | None, *, merged: bool) -> str:
    """Map a PR's live ``(merged, mergeable_state)`` to one routing decision — pure.

    ``clean``/already-merged -> push; ``dirty`` -> bounce; ``behind`` -> update-branch then
    wait; every other state -> wait, since none is a content conflict."""
    if merged:
        return _PUSH
    if mergeable_state == "clean":
        return _PUSH
    if mergeable_state == "dirty":
        return _BOUNCE
    if mergeable_state == "behind":
        return _UPDATE
    return _WAIT


def classify_checks(check_runs: list[dict[str, Any]]) -> str:
    """Map a commit's live check-run list to a terminal-vs-wait decision — pure.

    ``_FAILED`` fires when ANY run has completed with a terminal ``conclusion``, whether or
    not the forge would call that check required (issue #232). Every other shape — still
    running, empty, or malformed — degrades to ``_WAIT``; this never raises."""
    if not isinstance(check_runs, list):
        return _WAIT
    for run in check_runs:
        if not isinstance(run, dict):
            continue
        if run.get("status") == "completed" and run.get("conclusion") in _TERMINAL_CONCLUSIONS:
            return _FAILED
    return _WAIT


def render_findings(records: list[dict[str, Any]]) -> str:
    """Render a ``delivery-findings`` marker artifact body from per-repo poll records — pure.

    Each record carries ``repo``/``number``/``url``/``decision``/``checks``; a ``_FAILED``
    record's checks carry ``name``/``conclusion``/``details_url``/``base_red``, a ``_WAIT``
    record's ``name``/``status``. Plain markdown for a resolve worker to read, not JSON."""
    return "\n\n".join(_render_repo_findings(record) for record in records)


def _render_repo_findings(record: dict[str, Any]) -> str:
    """Render one repo's section of :func:`render_findings`'s output."""
    header = f"## {record['repo']}#{record['number']} — {record['url']}"
    if record["decision"] == _FAILED:
        lines = [header, "CI check failures:"]
        for check in record["checks"]:
            lines.append(f"  - {check['name']}: {check['conclusion']} — {check['details_url']}")
            base_red = check.get("base_red")
            if base_red is True:
                lines.append(f"    base branch: also failing {check['name']} — not this change")
            elif base_red is False:
                lines.append(f"    base branch: {check['name']} is clean — this change broke CI")
        return "\n".join(lines)
    lines = [header, "Still running:"]
    for check in record["checks"]:
        lines.append(f"  - {check['name']}: {check['status']}")
    return "\n".join(lines)


def _failing_checks(check_runs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The subset of ``check_runs`` :func:`classify_checks` would call terminal — pure,
    used to build the ``checks`` list a ``_FAILED`` :func:`render_findings` record names."""
    return [
        run
        for run in check_runs
        if isinstance(run, dict) and run.get("status") == "completed" and run.get("conclusion") in _TERMINAL_CONCLUSIONS
    ]


def _base_check_failed(base_checks: list[dict[str, Any]] | None, name: str) -> bool | None:
    """Whether the base branch's own latest check run named ``name`` is itself terminal —
    ``None`` when ``base_checks`` is ``None`` (the base read wasn't done or degraded, per
    :func:`render_findings`'s documented ``base_red`` contract), ``False`` when
    ``base_checks`` was read but carries no terminal run of that name."""
    if base_checks is None:
        return None
    for run in base_checks:
        if isinstance(run, dict) and run.get("name") == name:
            return run.get("status") == "completed" and run.get("conclusion") in _TERMINAL_CONCLUSIONS
    return False


class _Conflict(Exception):
    """Raised to abort the check stage as a real conflict — nothing has been merged."""


def main() -> int:
    """Run the land policy, aborting cleanly on an unconfirmed marker write.

    A :class:`MarkerWriteError` is caught HERE, not inside the per-repo loop, so a marker
    failure aborts the run instead of continuing to the next repo."""
    try:
        return _land()
    except MarkerWriteError as exc:
        print(f"marker write failed: {exc}", file=sys.stderr)
        return 1


def _land() -> int:
    forge_url = require_env(_ENV_FORGE_URL).rstrip("/")
    token = os.environ.get(_ENV_FORGE_TOKEN)
    owner = os.environ.get(_ENV_FORGE_OWNER, "")
    base_branch = require_env(_ENV_BASE_BRANCH)
    commits: list[dict[str, str]] = require_json_env(_ENV_GIT_COMMITS)
    already: set[str] = set(json.loads(os.environ.get(_ENV_ARTIFACT_NAMES, "[]")))
    callback_url = os.environ.get(_ENV_MARKER_CALLBACK_URL, "")
    marker_token = os.environ.get(_ENV_MARKER_TOKEN, "")
    feature_title = os.environ.get(_ENV_FEATURE_TITLE) or ""

    def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        return forge_request(method, f"{forge_url}{path}", token=token, body=body)

    def fetch_check_runs(repo: str, ref: str) -> list[dict[str, Any]] | None:
        """GET ``repo``'s check-runs at ``ref``, degrading to ``None`` on ANY read
        failure — a non-200 response, a malformed body, or an outright exception (the
        shape a real forge outage raises) — never a bounce or a crash (issue #232)."""
        try:
            status, payload = api("GET", f"/repos/{repo}/commits/{ref}/check-runs")
        except Exception:
            return None
        if status != 200 or not isinstance(payload, dict):
            return None
        check_runs = payload.get("check_runs")
        return check_runs if isinstance(check_runs, list) else None

    record_marker = marker_recorder(callback_url=callback_url, token=marker_token, request=forge_request)
    write_findings = post_marker(callback_url=callback_url, token=marker_token, request=forge_request)

    refuse_empty_delivery(commits)
    pending = [c for c in commits if f"{_MARKER_PREFIX}{c['repo']}" not in already]
    if not pending:
        print(_LANDED)
        return 0

    # --- check stage: no repo is merged unless ALL check `clean` (chunk atomicity), and
    #     the loop never short-circuits on a failure, so findings accumulate together.
    to_merge: list[tuple[str, str, int, str]] = []  # (bare repo, qualified repo, pr number, head sha)
    failures: list[dict[str, Any]] = []
    wait_records: list[dict[str, Any]] = []
    wait = False
    try:
        for commit in pending:
            bare_repo = commit["repo"]
            repo = qualify_repo(bare_repo, owner)
            branch = commit["branch"]
            status, listed = api("GET", f"/repos/{repo}/pulls?state=open")
            existing = next((p for p in (listed or []) if p.get("head", {}).get("ref") == branch), None)
            if existing is None:
                status, created = api(
                    "POST",
                    f"/repos/{repo}/pulls",
                    {
                        "title": pr_title(feature_title, branch),
                        "head": branch,
                        "base": base_branch,
                        "user": _HUB_USER,
                    },
                )
                if status != 201:
                    # A create hiccup is worth another poll, not a bounce.
                    print(f"could not open a PR for {repo}:{branch}: {created}", file=sys.stderr)
                    wait = True
                    continue
                existing = created
            number = int(existing["number"])
            status, pull = api("GET", f"/repos/{repo}/pulls/{number}")
            head_sha = (pull.get("head") or {}).get("sha") or commit["commit"]
            state = pull.get("mergeable_state")
            decision = classify(state, merged=bool(pull.get("merged")))
            if decision == _BOUNCE:
                raise _Conflict(f"{repo}#{number} is dirty (a real merge conflict)")
            if decision == _UPDATE:
                # Guarded on the head just read, so updates never stack. Any non-202 other
                # than a named conflict waits — the NEXT poll's state is authoritative.
                ustatus, ubody = api(
                    "PUT",
                    f"/repos/{repo}/pulls/{number}/update-branch",
                    {"expected_head_sha": head_sha},
                )
                message = (ubody or {}).get("message", "") if isinstance(ubody, dict) else ""
                if ustatus == 422 and "conflict" in message.lower():
                    raise _Conflict(f"{repo}#{number} update-branch reported a conflict: {message}")
                print(f"{repo}#{number} behind — update-branch requested (HTTP {ustatus}); re-polling", file=sys.stderr)
                wait = True
                continue
            if decision == _WAIT:
                if state in {"blocked", "unstable"}:
                    # The CI-watch case (issue #232): a degraded read (`None`) falls
                    # through to the plain wait below.
                    check_runs = fetch_check_runs(repo, head_sha)
                    if check_runs is not None and classify_checks(check_runs) == _FAILED:
                        base_checks = fetch_check_runs(repo, base_branch)
                        checks = [
                            {
                                "name": run.get("name"),
                                "conclusion": run.get("conclusion"),
                                "details_url": run.get("details_url"),
                                "base_red": _base_check_failed(base_checks, run.get("name", "")),
                            }
                            for run in _failing_checks(check_runs)
                        ]
                        failures.append(
                            {
                                "repo": repo,
                                "number": number,
                                "url": pull.get("html_url") or "",
                                "decision": _FAILED,
                                "checks": checks,
                            }
                        )
                        print(f"{repo}#{number} has a terminal CI check failure — will not re-poll", file=sys.stderr)
                        continue
                    if check_runs:
                        # Only a NON-empty read makes this poll "substantive": findings
                        # from a zero-check read say nothing (issue #232).
                        wait_records.append(
                            {
                                "repo": repo,
                                "number": number,
                                "url": pull.get("html_url") or "",
                                "decision": _WAIT,
                                "checks": [
                                    {"name": run.get("name"), "status": run.get("status")}
                                    for run in check_runs
                                    if isinstance(run, dict)
                                ],
                            }
                        )
                print(f"{repo}#{number} is {state} — not cleanly mergeable yet; re-polling", file=sys.stderr)
                wait = True
                continue
            # decision == _PUSH: clean (or already merged) — eligible.
            to_merge.append((bare_repo, repo, number, head_sha))
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print(_CONFLICT)
        return 0

    if failures:
        # Nothing merges (chunk atomicity). The write is deliberately unguarded, unlike
        # the wait path below: unwritten findings leave `resolve` nothing to read (#243).
        write_findings(_FINDINGS_NAME, render_findings(failures))
        print(_CI_FAILURE)
        return 0

    if wait:
        # Merge NOTHING (chunk atomicity). Findings are re-written every poll, and a write
        # failure here degrades to `pending` since the next poll re-writes them (#243).
        if wait_records:
            try:
                write_findings(_FINDINGS_NAME, render_findings(wait_records))
            except MarkerWriteError as exc:
                print(f"delivery-findings write failed (wait path, non-fatal): {exc}", file=sys.stderr)
        print(_PENDING)
        return 0

    # --- merge stage: merge the CURRENT head sha, which a self-heal update-branch may
    #     have advanced past the originally-recorded artifact commit.
    for bare_repo, repo, number, head_sha in to_merge:
        status, result = api(
            "PUT",
            f"/repos/{repo}/pulls/{number}/merge",
            {
                "commit_message": feature_title or f"blizzard: land {bare_repo}",
                "sha": head_sha,
                "merge_method": "merge",
                "user": _HUB_USER,
            },
        )
        landed_sha = (result or {}).get("sha")
        if status != 200 or not (result or {}).get("merged"):
            # An already-merged PR is a prior run's un-marked merge, a no-op to redo
            # (bzh:hub-node-step-idempotence); anything else is a race worth re-polling.
            _, pull = api("GET", f"/repos/{repo}/pulls/{number}")
            if not (pull or {}).get("merged"):
                print(f"merge of {repo}#{number} did not land ({result}); will re-poll", file=sys.stderr)
                print(_PENDING)
                return 0
            landed_sha = pull.get("merge_commit_sha") or head_sha
        record_marker(bare_repo, landed_sha or head_sha)

    print(_LANDED)
    return 0


def _selftest() -> int:
    """Assert the pure routing table — no network. The classification is the risk."""
    cases = [
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
    failures = 0
    for (state, merged), expected in cases:
        got = classify(state, merged=merged)
        ok = got == expected
        failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  ({state!r}, merged={merged}) -> {got}  (want {expected})")
    print(f"{'PASS' if not failures else 'FAIL'}: {len(cases) - failures}/{len(cases)} routing cases")

    check_cases = [
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
    check_failures = 0
    for check_runs, expected in check_cases:
        got = classify_checks(check_runs)
        ok = got == expected
        check_failures += not ok
        print(f"  {'ok ' if ok else 'FAIL'}  {check_runs!r} -> {got}  (want {expected})")
    print(
        f"{'PASS' if not check_failures else 'FAIL'}: "
        f"{len(check_cases) - check_failures}/{len(check_cases)} check-run cases"
    )

    total_failures = failures + check_failures
    return 1 if total_failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
