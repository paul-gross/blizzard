"""The default graph's `deliver` node script — the reference hub-node land policy.

**Chunk atomicity is this script's own property**: every repo the chunk submitted a
``git_commit`` for is CHECKED before ANY is pushed, so one dirty repo lands nothing; each
push then records a ``merged/<repo>`` marker (``bzh:hub-node-step-idempotence``).
"""

from __future__ import annotations

import sys

from blizzard.hub.graphs.scripts.land_common import LandRun, MarkerWriteError, pr_title

_HUB_USER = "blizzard-hub"


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
    run = LandRun.from_env()
    pending = run.pending()
    if not pending:
        print("landed")
        return 0

    try:
        # --- check stage: every pending repo must merge cleanly before any push ---
        to_push: list[tuple[str, str, int, str]] = []  # (bare repo, qualified repo, pr number, commit)
        for commit in pending:
            bare_repo = commit["repo"]
            repo = run.repo(bare_repo)
            branch = commit["branch"]
            status, listed = run.api("GET", f"/repos/{repo}/pulls?state=open")
            existing = next(
                (p for p in (listed or []) if p.get("head", {}).get("ref") == branch),
                None,
            )
            if existing is None:
                status, created = run.api(
                    "POST",
                    f"/repos/{repo}/pulls",
                    {
                        "title": pr_title(run.feature_title, branch),
                        "head": branch,
                        "base": run.base_branch,
                        "user": _HUB_USER,
                    },
                )
                if status != 201:
                    raise _Conflict(f"could not open a PR for {repo}:{branch}: {created}")
                existing = created
            number = int(existing["number"])
            status, pull = run.api("GET", f"/repos/{repo}/pulls/{number}")
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
            status, result = run.api(
                "PUT",
                f"/repos/{repo}/pulls/{number}/merge",
                {
                    "commit_message": run.feature_title or f"blizzard: land {bare_repo}",
                    "sha": commit_hash,
                    "merge_method": "merge",
                    "user": _HUB_USER,
                },
            )
            landed_sha = (result or {}).get("sha")
            if status != 200 or not (result or {}).get("merged"):
                # A kill between a prior run's push and its marker leaves the PR already
                # merged — re-pushing is a no-op (bzh:hub-node-step-idempotence).
                _, pull = run.api("GET", f"/repos/{repo}/pulls/{number}")
                if not (pull or {}).get("merged"):
                    raise _Conflict(f"merge of {repo}#{number} failed: {result}")
                landed_sha = pull.get("merge_commit_sha") or commit_hash
            run.markers.record(bare_repo, landed_sha or commit_hash)
            run.pause_for_crash_window(marker_index=marker_index, pending_count=pending_count)
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print("conflict")
        return 0

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
