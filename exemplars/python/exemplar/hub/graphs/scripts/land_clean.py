"""A ``deliver`` node script: merge every repo's PR once all of them read clean.

Every pending repo is checked before any is merged, so one dirty repo lands nothing; each merge records its
``merged/<repo>`` marker before the next begins."""

# exemplar: bzh:hub-node-outcome-protocol, bzh:hub-node-env-contract, bzh:hub-node-step-idempotence

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts.land_common import (
    LandedRevisionUnknown,
    LandRun,
    MarkerWriteError,
    MergeDidNotLand,
    NothingToLand,
    PullRequest,
    PullRequestLookupError,
    PullRequestOpenError,
)

_LANDED = "landed"
_CONFLICT = "conflict"
_PENDING = "pending"
_FAILURE = "failure"

_CLEAN_STATE = "clean"
#: The forge has not yet computed mergeability, so another poll may settle it.
_UNSETTLED_STATES = frozenset({None, "unknown"})


class _Conflict(Exception):
    """A repo cannot merge cleanly; nothing has been merged yet."""


class _Unsettled(Exception):
    """A repo's mergeability is not yet known."""


def main() -> int:
    """Land the chunk, exiting nonzero when a marker write is never confirmed."""
    try:
        print(_land())
    except MarkerWriteError as exc:
        print(f"marker write failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _land() -> str:
    run = LandRun.from_env()
    if (clash := run.duplicate_repo()) is not None:
        print(clash, file=sys.stderr)
        return _FAILURE
    try:
        ready = _check(run)
    except _Unsettled as exc:
        print(f"waiting: {exc}", file=sys.stderr)
        return _PENDING
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        return _CONFLICT
    for marker_index, (pull, commit_hash) in enumerate(ready, start=1):
        try:
            landed_sha = pull.merge(commit_hash)
        except (MergeDidNotLand, LandedRevisionUnknown) as exc:
            print(f"merge of {pull} did not land: {exc}", file=sys.stderr)
            return _CONFLICT
        run.markers.record(pull.bare_repo, landed_sha)
        run.pause_for_crash_window(marker_index=marker_index, pending_count=len(ready))
    return _LANDED


def _check(run: LandRun) -> list[tuple[PullRequest, str]]:
    ready: list[tuple[PullRequest, str]] = []
    for commit in run.pending():
        try:
            pull = PullRequest.of(run, commit)
        except NothingToLand as exc:
            print(f"{exc} — nothing to land", file=sys.stderr)
            run.markers.record(commit["repo"], exc.landed_sha)
            continue
        except (PullRequestOpenError, PullRequestLookupError, LandedRevisionUnknown) as exc:
            raise _Conflict(str(exc)) from exc
        # A merged PR is an interrupted run's own landing; merging it again returns its revision.
        if not pull.merged and pull.mergeable_state in _UNSETTLED_STATES:
            raise _Unsettled(f"{pull} has no mergeable state yet")
        if not pull.merged and pull.mergeable_state != _CLEAN_STATE:
            raise _Conflict(f"{pull} is {pull.mergeable_state}")
        ready.append((pull, commit["commit"]))
    return ready


if __name__ == "__main__":
    sys.exit(main())
