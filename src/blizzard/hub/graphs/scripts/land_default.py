"""The default graph's `deliver` node script — the reference hub-node land policy.

**Chunk atomicity is this script's own property**: every repo the chunk submitted a
``git_commit`` for is CHECKED before ANY is pushed, so one dirty repo lands nothing; each
push then records a ``merged/<repo>`` marker (``bzh:hub-node-step-idempotence``).
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from blizzard.hub.graphs.scripts.land_common import (
    _MARKER_PREFIX,
    MarkerWriteError,
    forge_request,
    marker_recorder,
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
_ENV_EXPECT_GIT_COMMITS = "BZ_HUB_EXPECT_GIT_COMMITS"

# Test-only: seconds to pause after the first repo's marker, widening the wall-clock
# between-pushes kill window for tests/crash/test_kill9_sweep.py. Inert unless set.
_ENV_TEST_PAUSE_AFTER_FIRST_MARKER = "BZ_HUB_LAND_TEST_PAUSE_SECONDS"

_HUB_USER = "blizzard-hub"


def _test_pause_after_first_marker(*, marker_index: int, pending_count: int) -> None:
    """Widen the between-repo-pushes window for the mid-script crash sweep — test-only.

    Inert unless :data:`_ENV_TEST_PAUSE_AFTER_FIRST_MARKER` names a positive number of
    seconds, and fires only after the first marker of a genuinely multi-repo push."""
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
    """Run the land policy, aborting cleanly on an unconfirmed marker write.

    A single top-level catch, not one inside the per-repo loop: a marker failure aborts
    the rest of the run, and ``landed`` is never printed once one has fired."""
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

    record_marker = marker_recorder(callback_url=callback_url, token=marker_token, request=forge_request)

    refuse_empty_delivery(commits)
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
                # A kill between a prior run's push and its marker leaves the PR already
                # merged — re-pushing is a no-op (bzh:hub-node-step-idempotence).
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
