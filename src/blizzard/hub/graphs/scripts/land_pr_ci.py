"""The PR + CI-watch delivery policy's `deliver` node script — self-healing.

This alternative delivery policy backs the `advanced-development-workflow` graph's
`deliver` node (`hub/graphs/advanced-development-workflow/graph.yaml`), proving delivery
policy lives in YAML. It honors the same hub-command-node authoring contract as the default land script
(``blizzard-context:/standards/hub-nodes.md``): pure stdlib against the forge, env
injected by the executor, the authored choice (``landed``/``conflict``) or the reserved
``pending`` printed as the LAST stdout line, diagnostics to stderr, exit 0 for every
outcome the policy can express — an EMPTY commit set from a graph that declared a
``git_commit`` somewhere being the one exception, a defect upstream of delivery that
exits non-zero so the engine routes ``failure``
(:func:`~blizzard.hub.graphs.scripts.land_default.refuse_empty_delivery`).

Unlike the default graph's strict one-shot (:mod:`blizzard.hub.graphs.scripts.land_default`),
this opens a PR per repo and routes by the PR's live ``mergeable_state`` — resolving what
is mechanical or transient without ever waking the LLM:

    clean               -> merge it
    unknown             -> "pending"  (GitHub still computing mergeability — re-poll)
    behind              -> PUT .../update-branch, then "pending"  (base moved, no conflict — self-heal)
    blocked/unstable    -> "pending"  (required CI/reviews not green yet — WAIT, the CI-watch case)
    blocked/unstable, a check run completed terminally -> "failure"  (never going green — issue #232)
    dirty               -> "conflict"  (a real merge conflict — the ONE true LLM bounce)

``behind`` already implies ``mergeable: true`` (a *conflicting* stale branch is ``dirty``,
never ``behind``), so ``update-branch`` is conflict-free at compute time; a losing race
(base moves with a conflict between our read and the update) surfaces on the NEXT poll as
``dirty`` -> ``conflict``, so conflicts can never slip through and clean-but-stale PRs land
themselves. Every ``pending`` frees the fleet-wide hub-execution slot between polls so
other chunks' hub nodes run in the gap. ``poll_timeout`` is the executor's job
(``bzh:hub-node-outcome-protocol``): its expiry fires the engine's ``failure`` kick-back —
so the graph MUST author a ``failure`` edge, and (for the ``dirty`` fast-bounce) a
``conflict`` edge.

Same env contract as :mod:`~blizzard.hub.graphs.scripts.land_default`
(``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``/``BZ_HUB_BASE_BRANCH``/
``BZ_HUB_GIT_COMMITS``/``BZ_HUB_ARTIFACT_NAMES``/``BZ_HUB_MARKER_CALLBACK_URL``/
``BZ_HUB_FEATURE_TITLE``, optional). Run ``python3 -m blizzard.hub.graphs.scripts.land_pr_ci
--selftest`` to exercise the pure routing table with no network.
"""

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
# The graph's authored `failure` choice on the `deliver` node (issue #232) —
# advanced-development-workflow/graph.yaml's `deliver.judgement.choices` names exactly
# `landed`/`failure`, so a terminal CI check failure must print THIS literal string, not
# `_FAILED` (below), which is an internal per-repo decision spelled differently ("failed").
_CI_FAILURE = "failure"

# The marker name a terminal-CI-failure or a substantive wait writes its findings under —
# a resolve worker reads this artifact directly (issue #232).
_FINDINGS_NAME = "delivery-findings"

# Pure routing decisions (what to do with one repo after reading its live PR).
_PUSH = "push"  # clean (or already merged) — eligible for the merge stage
_WAIT = "wait"  # unknown / required-checks-not-green / … — re-poll, no side effect
_UPDATE = "update"  # behind — fire update-branch, then re-poll
_BOUNCE = "bounce"  # dirty — a real content conflict, kick back to build
_FAILED = "failed"  # a check run completed with a terminal conclusion — never re-poll

# `classify_checks`' terminal conclusions (issue #232): a completed check run in any of
# these is never going to turn green on its own — polling `mergeable_state` out to
# `poll_timeout` just burns the slot instead of surfacing the failure.
_TERMINAL_CONCLUSIONS = {"failure", "timed_out", "cancelled", "action_required"}


def classify(mergeable_state: str | None, *, merged: bool) -> str:
    """Map a PR's live ``(merged, mergeable_state)`` to one routing decision — pure.

    The whole risk of this script lives here, so it is a network-free function the
    ``--selftest`` mode asserts against. ``clean``/already-merged -> push; ``dirty`` ->
    bounce (the only true LLM kick-back); ``behind`` -> update-branch then wait;
    everything else (``unknown``, ``blocked``, ``unstable``, ``has_hooks``, ``draft``,
    missing) -> wait, because none is a content conflict — the CI-watch case (``blocked``/
    ``unstable``) is exactly a wait *within this function*; the caller layers a further
    :func:`classify_checks` read on top of a ``blocked``/``unstable`` wait to catch a check
    run that has already failed terminally (issue #232) — and the node's ``poll_timeout``
    is the backstop for whatever still isn't resolved."""
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

    Mirrors :func:`classify`'s role for ``mergeable_state``, one level lower: given the
    ``check_runs`` array from ``GET /repos/{owner}/{repo}/commits/{ref}/check-runs`` (each a
    dict carrying at least ``status`` and ``conclusion``), ``_FAILED`` fires when ANY run has
    completed with a terminal ``conclusion`` (``failure``/``timed_out``/``cancelled``/
    ``action_required``) — a check that will never turn green on its own, so burning the rest
    of ``poll_timeout`` on it is pure waste. Every other shape — runs still ``queued``/
    ``in_progress``/``waiting``/``requested``, an empty list, or a malformed payload (missing
    keys, wrong types) — degrades to ``_WAIT``; this function never raises.

    Design decision D1 (issue #232): terminal regardless of whether GitHub would call the
    check "required" — that distinction needs a branch-protection read this script doesn't
    do, and ``clean`` is its only merge gate anyway (pinned by ``tests/test_land_scripts.py``'s
    ``test_classify_checks_is_failed_for_every_terminal_conclusion``)."""
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

    Plain text/markdown (not JSON) meant for a resolve worker to read directly. Each element
    of ``records`` describes one repo's PR at poll time::

        {
            "repo": str,                  # qualified repo, e.g. "acme/widget"
            "number": int,                # PR number
            "url": str,                   # PR's html_url
            "decision": _FAILED | _WAIT,  # which case this repo is reported in
            "checks": [...],              # see below — shape depends on `decision`
        }

    When ``decision == _FAILED``, each element of ``checks`` is::

        {
            "name": str,
            "conclusion": str,           # the terminal conclusion classify_checks matched
            "details_url": str,
            "base_red": bool | None,     # whether the base branch's own latest check run of
                                          # the SAME name also fails — "the base's gate was
                                          # already red" (True) vs "this change broke CI"
                                          # (False); None when that lookup wasn't done.
        }

    When ``decision == _WAIT``, each element of ``checks`` is::

        {"name": str, "status": str}     # still queued/in_progress/waiting/requested

    Pure text formatting — no I/O — and independent of `main`'s own dict shapes beyond what's
    documented here, so a later wiring phase is free to build these records however is
    convenient there."""
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

    A :class:`MarkerWriteError` raised anywhere inside :func:`_land` (always from the
    merge stage, after at least one repo's merge) is caught HERE — a single top-level
    catch, not inside the per-repo loop — so a marker failure aborts the rest of the run
    instead of quietly continuing to the next repo, and ``landed`` is never printed once
    one has fired."""
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

    # --- check stage: read every pending repo's live PR state; decide per repo. No repo
    #     is merged unless ALL check `clean` (chunk atomicity). A `dirty` short-circuits
    #     to `conflict`; a `behind` self-heals via update-branch; unknown/CI-not-green wait.
    #     `failures`/`wait_records` (issue #232) accumulate `render_findings` records for a
    #     terminally-failed check run and a substantive wait, respectively — the loop never
    #     short-circuits on a failure, so every pending repo's findings are named together.
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
                    # A freshly opened PR often reads `unknown` before GitHub computes
                    # mergeability; a create hiccup is likewise worth another poll, not a
                    # bounce. Wait and re-poll rather than treating it as a conflict.
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
                # base advanced with no conflict — ask GitHub to merge base into head
                # (async, 202 Accepted), guarded on the head we just read so we never
                # stack updates. A 422 naming a conflict is a fast-path bounce; any other
                # non-202 just waits — the NEXT poll's mergeable_state is the source of
                # truth (a genuine race surfaces there as `dirty`).
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
                    # The CI-watch case: read the head's own check runs and see whether
                    # any has already completed terminally — never re-poll one out to
                    # `poll_timeout` (issue #232). A degraded read (`None`) falls straight
                    # through to the plain wait below, unchanged from today's behavior.
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
                        # D2 (issue #232): only a NON-empty check-runs read makes this poll
                        # "substantive" — a fresh PR's first poll often reads zero check
                        # runs, and findings written then say nothing (pinned by
                        # tests/test_land_scripts.py::test_an_empty_check_runs_list_is_not_a_substantive_wait).
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
        # At least one repo's check run completed terminally — never worth re-polling
        # out to `poll_timeout`. Nothing merges (chunk atomicity, same as `wait` below);
        # write the findings so the resolve worker can read exactly what failed.
        #
        # Deliberately left unguarded, unlike the wait-path write below: an unwritten set
        # of findings is the only signal `resolve` has nothing to read, and this branch
        # routes to `failure` either way (issue #243; pinned by tests/test_pin_hub_delivery.py::
        # test_a_terminal_failure_findings_write_failure_exits_non_zero_instead_of_routing_failure).
        write_findings(_FINDINGS_NAME, render_findings(failures))
        print(_CI_FAILURE)
        return 0

    if wait:
        # At least one repo is not cleanly mergeable yet (unknown/behind/CI-not-green) —
        # release the hub slot and re-poll; merge NOTHING (chunk atomicity). A
        # substantive wait (D2) writes its in-flight findings on EVERY poll — the
        # callback's own (chunk, node, name, epoch) idempotence makes a repeat write
        # within one visit a no-op, so this is not gated on `already`/artifact names,
        # which is scoped by node, not epoch, and would wrongly suppress every visit
        # after the first.
        #
        # Unlike the terminal-failure write above, a failure HERE degrades to `pending`:
        # the next poll re-writes the same findings, so it costs only diagnosability and
        # must not bounce a healthy chunk (issue #243; pinned by
        # tests/test_land_scripts.py::test_a_wait_path_findings_write_failure_degrades_to_a_plain_pending_not_a_bounce).
        if wait_records:
            try:
                write_findings(_FINDINGS_NAME, render_findings(wait_records))
            except MarkerWriteError as exc:
                print(f"delivery-findings write failed (wait path, non-fatal): {exc}", file=sys.stderr)
        print(_PENDING)
        return 0

    # --- merge stage: every repo checked clean — merge each, marking as we go. Merge the
    #     CURRENT head sha (which a self-heal update-branch may have advanced past the
    #     originally-recorded commit), not the stale artifact commit.
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
            # A kill between a prior run's merge and its marker leaves the PR already
            # merged — re-merging is a no-op (bzh:hub-node-step-idempotence). Otherwise a
            # transient merge race (head moved, mergeability recomputing) is worth another
            # poll rather than a bounce — the next poll re-derives the state cleanly.
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
