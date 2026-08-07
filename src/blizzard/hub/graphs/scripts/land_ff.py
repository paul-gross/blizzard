"""A PR-free, fast-forward `deliver` node script — no merge commit, linear history.

Advances each repo's base ref directly to the chunk's own commit with ``force: false``,
so the forge rejects a non-fast-forward with a 422 — exactly what must happen when the
base moved under a rebased chunk. Repos update one at a time, so a rejection on repo N
leaves ``1..N-1`` advanced: an accepted PARTIAL land, recovered by markers and a re-run."""

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts.land_common import LandRun, MarkerWriteError


class _Conflict(Exception):
    """Raised to abort the run — either pre-flight (nothing has been updated yet) or the
    update stage itself (everything before the raising repo has already been updated and
    marked — a partial land, see the module docstring)."""


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
        print("landed")
        return 0

    try:
        # --- pre-flight: read every pending repo's current base ref before ANY update ---
        current_shas: dict[str, str] = {}
        for commit in pending:
            bare_repo = commit["repo"]
            repo = run.repo(bare_repo)
            status, ref = run.api("GET", f"/repos/{repo}/git/ref/heads/{run.base_branch}")
            if status != 200:
                raise _Conflict(f"could not read the {run.base_branch} ref for {repo}: {ref}")
            sha = (ref or {}).get("object", {}).get("sha")
            if not sha:
                raise _Conflict(f"{repo}'s {run.base_branch} ref has no resolvable sha: {ref}")
            current_shas[bare_repo] = sha

        # --- update stage: fast-forward every repo's base ref to its own commit ---
        pending_count = len(pending)
        for marker_index, commit in enumerate(pending, start=1):
            bare_repo = commit["repo"]
            repo = run.repo(bare_repo)
            target = commit["commit"]
            if current_shas[bare_repo] == target:
                # Crash recovery: a prior run advanced this ref but the kill hit before its
                # marker became durable — a no-op success (bzh:hub-node-step-idempotence).
                run.markers.record(bare_repo, target)
                run.pause_for_crash_window(marker_index=marker_index, pending_count=pending_count)
                continue
            status, result = run.api(
                "PATCH",
                f"/repos/{repo}/git/refs/heads/{run.base_branch}",
                {"sha": target, "force": False},
            )
            if status != 200:
                raise _Conflict(f"could not fast-forward {repo}'s {run.base_branch} to {target}: {result}")
            run.markers.record(bare_repo, target)
            run.pause_for_crash_window(marker_index=marker_index, pending_count=pending_count)
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print("conflict")
        return 0

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
