"""The default graph's `deliver` node script — the reference hub-node land policy.

**Chunk atomicity is this script's own property**: every repo the chunk submitted a
``git_commit`` for is CHECKED before ANY is pushed, so one dirty repo lands nothing; each
push then records a ``merged/<repo>`` marker (``bzh:hub-node-step-idempotence``).
"""

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts.land_common import (
    LandRun,
    MarkerWriteError,
    MergeDidNotLand,
    PullRequest,
    PullRequestOpenError,
)


class _Conflict(Exception):
    """Raised to abort the check stage — nothing has been pushed yet."""


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
        # --- check stage: every pending repo must merge cleanly before any push ---
        to_push: list[tuple[PullRequest, str]] = []
        for commit in pending:
            try:
                pull = PullRequest.of(run, commit)
            except PullRequestOpenError as exc:
                raise _Conflict(str(exc)) from exc
            # An already-merged PR is a prior, interrupted run's — nothing to check, since
            # the push stage below re-derives its outcome as a no-op.
            if not pull.merged and pull.mergeable_state != "clean":
                raise _Conflict(f"{pull} is {pull.mergeable_state}, not mergeable cleanly")
            to_push.append((pull, commit["commit"]))

        # --- push stage: every repo checked clean (or already landed) — merge all ---
        pending_count = len(to_push)
        for marker_index, (pull, commit_hash) in enumerate(to_push, start=1):
            try:
                landed_sha = pull.merge(commit_hash)
            except MergeDidNotLand as exc:
                raise _Conflict(f"merge of {pull} failed: {exc.result}") from exc
            run.markers.record(pull.bare_repo, landed_sha)
            run.pause_for_crash_window(marker_index=marker_index, pending_count=pending_count)
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print("conflict")
        return 0

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
