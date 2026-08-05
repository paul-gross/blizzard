"""A PR-free, fast-forward `deliver` node script — no merge commit, linear history.

Advances each repo's base ref directly to the chunk's own commit with ``force: false``,
so the forge rejects a non-fast-forward with a 422 — exactly what must happen when the
base moved under a rebased chunk. Repos update one at a time, so a rejection on repo N
leaves ``1..N-1`` advanced: an accepted PARTIAL land, recovered by markers and a re-run."""

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

# Test-only instrumentation for the mid-script crash sweep: the between-repo-updates
# window is a wall-clock race a `kill -9` must land inside, so a positive value widens it.
_ENV_TEST_PAUSE_AFTER_FIRST_MARKER = "BZ_HUB_LAND_TEST_PAUSE_SECONDS"


def _test_pause_after_first_marker(*, marker_index: int, pending_count: int) -> None:
    """Widen the between-repo-updates window for the mid-script crash sweep — test-only.

    Inert unless :data:`_ENV_TEST_PAUSE_AFTER_FIRST_MARKER` names a positive number of
    seconds, and fires only after the FIRST repo's marker on a genuinely multi-repo
    update, so a crash-recovery re-run never pauses."""
    raw = os.environ.get(_ENV_TEST_PAUSE_AFTER_FIRST_MARKER)
    if not raw or marker_index != 1 or pending_count < 2:
        return
    seconds = float(raw)
    if seconds > 0:
        print(f"[test] pausing {seconds}s after the first marker to widen the crash window", file=sys.stderr)
        time.sleep(seconds)


class _Conflict(Exception):
    """Raised to abort the run — either pre-flight (nothing has been updated yet) or the
    update stage itself (everything before the raising repo has already been updated and
    marked — a partial land, see the module docstring)."""


def main() -> int:
    """Run the land policy, aborting cleanly on an unconfirmed marker write.

    A :class:`MarkerWriteError` is caught HERE — a single top-level catch, not inside the
    per-repo loop — so a marker failure aborts the rest of the run instead of continuing
    to the next repo, and ``landed`` is never printed once one has fired."""
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

    def api(method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        return forge_request(method, f"{forge_url}{path}", token=token, body=body)

    record_marker = marker_recorder(callback_url=callback_url, token=marker_token, request=forge_request)

    refuse_empty_delivery(commits)
    pending = [c for c in commits if f"{_MARKER_PREFIX}{c['repo']}" not in already]
    if not pending:
        print("landed")
        return 0

    try:
        # --- pre-flight: read every pending repo's current base ref before ANY update ---
        current_shas: dict[str, str] = {}
        for commit in pending:
            bare_repo = commit["repo"]
            repo = qualify_repo(bare_repo, owner)
            status, ref = api("GET", f"/repos/{repo}/git/ref/heads/{base_branch}")
            if status != 200:
                raise _Conflict(f"could not read the {base_branch} ref for {repo}: {ref}")
            sha = (ref or {}).get("object", {}).get("sha")
            if not sha:
                raise _Conflict(f"{repo}'s {base_branch} ref has no resolvable sha: {ref}")
            current_shas[bare_repo] = sha

        # --- update stage: fast-forward every repo's base ref to its own commit ---
        pending_count = len(pending)
        for marker_index, commit in enumerate(pending, start=1):
            bare_repo = commit["repo"]
            repo = qualify_repo(bare_repo, owner)
            target = commit["commit"]
            if current_shas[bare_repo] == target:
                # Crash recovery: a prior run advanced this ref but the kill hit before its
                # marker became durable — a no-op success (bzh:hub-node-step-idempotence).
                record_marker(bare_repo, target)
                _test_pause_after_first_marker(marker_index=marker_index, pending_count=pending_count)
                continue
            status, result = api(
                "PATCH",
                f"/repos/{repo}/git/refs/heads/{base_branch}",
                {"sha": target, "force": False},
            )
            if status != 200:
                raise _Conflict(f"could not fast-forward {repo}'s {base_branch} to {target}: {result}")
            record_marker(bare_repo, target)
            _test_pause_after_first_marker(marker_index=marker_index, pending_count=pending_count)
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print("conflict")
        return 0

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
