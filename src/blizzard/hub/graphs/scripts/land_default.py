"""The default graph's `deliver` node script — fetch, check-all, push-all.

The reference hub-command-node `run:` script; it honors the authoring contract owned by
``blizzard-harness:/standards/hub-nodes.md`` (``bzh:hub-node-run-shape``,
``bzh:hub-node-env-contract``, ``bzh:hub-node-outcome-protocol``,
``bzh:hub-node-step-idempotence``).

Invoked by the packaged default graph's `deliver` hub command node
(``hub/graphs/default.yaml``) as ``python3 -m blizzard.hub.graphs.scripts.land_default``.
Talks to the forge itself, through the env a hub command node's executor injects
(``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``/``BZ_HUB_FEATURE_TITLE``,
optional) plus stdlib ``urllib`` — the hub engine never calls a forge seam for this
node (``bzh:deterministic-shell``): this IS the policy, expressed as data the operator
can re-author without touching blizzard code.

**Chunk atomicity is this script's own property, not the engine's**: every repo the
chunk submitted a ``git_commit`` pointer for is CHECKED first (opened or reused as a
PR, its live ``mergeable_state`` read) before ANY of them is pushed. A single dirty
repo prints ``conflict`` and returns before the push stage ever starts — nothing
lands. Only once every repo checks clean does the push stage run, merging each PR and
recording a ``merged/<repo>`` marker (via the mid-run callback) immediately after each
push — so a re-run (after a crash, or a fresh poll) skips every repo whose marker is
already durable (:data:`BZ_HUB_ARTIFACT_NAMES`) and, if a push itself lands but the
kill lands between that push and its marker, treats the PR's own already-merged state
as success rather than a conflict (re-pushing a landed merge is a no-op — the
at-least-once-per-step crash contract, ``bzh:hub-node-step-idempotence``).

Exit code is always 0: the node's authored choice — ``landed`` or ``conflict`` — is
the LAST line printed to stdout (``bzh:hub-node-outcome-protocol``); every diagnostic
goes to stderr so it never contaminates that line.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_FEATURE_TITLE = "BZ_HUB_FEATURE_TITLE"

# Test-only instrumentation for the mid-script crash sweep
# (``tests/crash/test_kill9_sweep.py::test_kill9_between_default_graph_repo_pushes``).
# Because this script loops over an arbitrary, chunk-dynamic number of repos inside ONE
# ``run:`` step — recording each ``merged/<repo>`` marker through the mid-run callback,
# not the executor's static per-step ``produces:`` — its "kill between two repos'
# pushes" window is a WALL-CLOCK race an external ``kill -9`` of the hub daemon must land
# inside, not a named in-process ``hubnode.*`` registry point. When set to a positive
# number of seconds, the script pauses that long immediately after recording the FIRST
# repo's marker on a multi-repo run, widening that window so the kill is deterministic.
# It fires at most once and never on a crash-recovery re-run (which pushes only the
# still-unmarked remainder, so ``pending_count`` is then 1), and is wholly inert unless
# the env var is set — never present in a production land.
_ENV_TEST_PAUSE_AFTER_FIRST_MARKER = "BZ_HUB_LAND_TEST_PAUSE_SECONDS"

_HUB_USER = "blizzard-hub"
_MARKER_PREFIX = "merged/"

# GitHub caps PR/issue titles at 256 characters; a resolved feature title longer than
# that is truncated with an ellipsis so PR creation never fails on an over-long title.
_PR_TITLE_MAX = 256


def pr_title(feature_title: str, branch: str) -> str:
    """The opened PR's title: JUST the hub-resolved feature title, or the branch name
    when none resolved — never a ``blizzard: land`` prefix — truncated to
    :data:`_PR_TITLE_MAX`."""
    title = feature_title or branch
    if len(title) > _PR_TITLE_MAX:
        title = title[: _PR_TITLE_MAX - 1].rstrip() + "…"
    return title


def qualify_repo(repo: str, owner: str) -> str:
    """``owner/name`` a forge route resolves."""
    if "/" in repo or not owner:
        return repo
    return f"{owner}/{repo}"


def forge_request(method: str, url: str, *, token: str | None, body: dict[str, Any] | None) -> tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"token {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw) if raw else {}
        except ValueError:
            payload = {"message": raw.decode(errors="replace")}
        return exc.code, payload


def _test_pause_after_first_marker(*, marker_index: int, pending_count: int) -> None:
    """Widen the between-repo-pushes window for the mid-script crash sweep — test-only.

    Inert unless :data:`_ENV_TEST_PAUSE_AFTER_FIRST_MARKER` names a positive number of
    seconds. Fires only after the FIRST repo's marker (``marker_index == 1``) on a
    genuinely multi-repo push (``pending_count >= 2``) — so a crash-recovery re-run, which
    pushes only the still-unmarked remainder (``pending_count == 1`` for a 2-repo chunk),
    never pauses and the script converges immediately."""
    raw = os.environ.get(_ENV_TEST_PAUSE_AFTER_FIRST_MARKER)
    if not raw or marker_index != 1 or pending_count < 2:
        return
    seconds = float(raw)
    if seconds > 0:
        print(f"[test] pausing {seconds}s after the first marker to widen the crash window", file=sys.stderr)
        time.sleep(seconds)


class _Conflict(Exception):
    """Raised to abort the check stage — nothing has been pushed yet."""


def main() -> int:
    forge_url = os.environ[_ENV_FORGE_URL].rstrip("/")
    token = os.environ.get(_ENV_FORGE_TOKEN)
    owner = os.environ.get(_ENV_FORGE_OWNER, "")
    base_branch = os.environ[_ENV_BASE_BRANCH]
    commits: list[dict[str, str]] = json.loads(os.environ[_ENV_GIT_COMMITS])
    already: set[str] = set(json.loads(os.environ.get(_ENV_ARTIFACT_NAMES, "[]")))
    callback_url = os.environ.get(_ENV_MARKER_CALLBACK_URL, "")
    feature_title = os.environ.get(_ENV_FEATURE_TITLE) or ""

    def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        return forge_request(method, f"{forge_url}{path}", token=token, body=body)

    def record_marker(repo: str, commit_hash: str) -> None:
        if not callback_url:
            return
        forge_request(
            "POST", callback_url, token=None, body={"name": f"{_MARKER_PREFIX}{repo}", "content": commit_hash}
        )

    pending = [c for c in commits if f"{_MARKER_PREFIX}{c['repo']}" not in already]
    if not pending:
        print("landed")
        return 0

    try:
        # --- check stage: every pending repo must merge cleanly before any push ---
        to_push: list[tuple[str, str, int, str]] = []  # (bare repo, qualified repo, pr number, commit)
        for commit in pending:
            bare_repo = commit["repo"]
            repo = qualify_repo(bare_repo, owner)
            branch = commit["branch"]
            status, listed = api("GET", f"/repos/{repo}/pulls?state=open")
            existing = next(
                (p for p in (listed or []) if p.get("head", {}).get("ref") == branch),
                None,
            )
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
                    raise _Conflict(f"could not open a PR for {repo}:{branch}: {created}")
                existing = created
            number = int(existing["number"])
            status, pull = api("GET", f"/repos/{repo}/pulls/{number}")
            if pull.get("merged"):
                # Already landed by a prior, interrupted run — nothing to check; the
                # push stage below re-derives its outcome as a no-op.
                to_push.append((bare_repo, repo, number, commit["commit"]))
                continue
            if pull.get("mergeable_state") != "clean":
                raise _Conflict(f"{repo}#{number} is {pull.get('mergeable_state')}, not mergeable cleanly")
            to_push.append((bare_repo, repo, number, commit["commit"]))

        # --- push stage: every repo checked clean (or already landed) — merge all ---
        pending_count = len(to_push)
        for marker_index, (bare_repo, repo, number, commit_hash) in enumerate(to_push, start=1):
            status, result = api(
                "PUT",
                f"/repos/{repo}/pulls/{number}/merge",
                {
                    "commit_message": feature_title or f"blizzard: land {bare_repo}",
                    "sha": commit_hash,
                    "merge_method": "merge",
                    "user": _HUB_USER,
                },
            )
            landed_sha = (result or {}).get("sha")
            if status != 200 or not (result or {}).get("merged"):
                # A kill between a prior run's push and its marker record leaves the PR
                # already merged — re-pushing it is a no-op (the at-least-once-per-step
                # contract, bzh:hub-node-step-idempotence), not a fresh conflict. Any
                # other failure IS a conflict.
                _, pull = api("GET", f"/repos/{repo}/pulls/{number}")
                if not (pull or {}).get("merged"):
                    raise _Conflict(f"merge of {repo}#{number} failed: {result}")
                landed_sha = pull.get("merge_commit_sha") or commit_hash
            record_marker(bare_repo, landed_sha or commit_hash)
            _test_pause_after_first_marker(marker_index=marker_index, pending_count=pending_count)
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print("conflict")
        return 0

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
