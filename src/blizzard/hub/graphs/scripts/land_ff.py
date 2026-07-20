"""A PR-free, fast-forward `deliver` node script — no merge commit, linear history.

Honors the same hub-command-node authoring contract as
:mod:`~blizzard.hub.graphs.scripts.land_default`
(``blizzard-harness:/standards/hub-nodes.md``: ``bzh:hub-node-run-shape``,
``bzh:hub-node-env-contract``, ``bzh:hub-node-outcome-protocol``,
``bzh:hub-node-step-idempotence``) but a different delivery *policy*: where
``land_default`` opens a PR per repo and merges it (producing a merge commit),
this script advances each repo's base branch ref directly to the chunk's own commit —

    PATCH /repos/{owner}/{repo}/git/refs/heads/{base_branch}   {"sha": <commit>, "force": false}

No PR is ever opened, read, or merged; ``force: false`` is the safety property, not an
incidental flag — the forge rejects any update that is not a fast-forward with a 422, and
that rejection is exactly what must happen when the base moved out from under a chunk that
rebased against a now-stale tip. Nothing should land in that case, and nothing does.

**Chunk atomicity is this script's own property, not the engine's**, and it is *weaker*
than ``land_default``'s: ``land_default`` checks every repo before pushing any of them, so
its failure mode is all-or-nothing. This script has no analogous check to run — a fast-
forward's only precondition is the live base ref, which can only be read by asking the
forge to update it — so repos are updated ONE AT A TIME, in submission order. A rejection
on repo N leaves repos ``1..N-1`` already advanced: a PARTIAL land. This is a KNOWN,
ACCEPTED property (it matches ``land_default``'s own push-stage partiality), not something
this docstring is hiding: recovery is markers plus a re-run — every repo already advanced
records its ``merged/<repo>`` marker (via the mid-run callback) immediately after its own
update, so a re-run (after a crash, or after the chunk re-rebases past the rejecting repo)
skips every repo whose marker is already durable (:data:`BZ_HUB_ARTIFACT_NAMES`) and only
retries the remainder.

A pre-flight stage reads every pending repo's CURRENT base-ref sha (``GET
/repos/{o}/{r}/git/ref/heads/{base}``) before any repo is updated, so a repo that is
unreachable or whose base branch does not exist fails before a partial land begins, exactly
as ``land_default``'s check stage runs before its push stage. Where a pending repo's
current ref already reads as the chunk's own target commit — the crash-recovery case, where
a prior run's update landed but the kill hit before its marker became durable — that repo is
treated as an immediate, no-op SUCCESS and its marker is (re-)recorded; no PATCH is issued
for it. This is not a special case bolted on to paper over an error: the forge's own
fast-forward semantics already treat an X-\\>X update as a no-op success, so a naive retry
would converge to the same place — resolving it during pre-flight just spares a redundant
network call and keeps the update stage itself simple (every repo it visits genuinely needs
a ref moved).

Same env contract as :mod:`~blizzard.hub.graphs.scripts.land_default`
(``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``/``BZ_HUB_BASE_BRANCH``/
``BZ_HUB_GIT_COMMITS``/``BZ_HUB_ARTIFACT_NAMES``/``BZ_HUB_MARKER_CALLBACK_URL``) — no
``BZ_HUB_FEATURE_TITLE``, since no PR or merge commit is ever authored here to title.

Exit code is always 0: the node's authored choice — ``landed`` or ``conflict`` — is the
LAST line printed to stdout (``bzh:hub-node-outcome-protocol``); every diagnostic goes to
stderr so it never contaminates that line.
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

from blizzard.hub.graphs.scripts.land_default import forge_request, qualify_repo

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"

# Test-only instrumentation for the mid-script crash sweep
# (``tests/crash/test_kill9_sweep.py::test_kill9_between_ff_graph_repo_pushes``) — the
# same mechanism and env var as ``land_default``'s own hook
# (:func:`~blizzard.hub.graphs.scripts.land_default._test_pause_after_first_marker`),
# duplicated here rather than shared because this script's update stage has its own
# loop and its own no-op (already-advanced) branch to pause after. Because this script
# loops over an arbitrary, chunk-dynamic number of repos inside ONE ``run:`` step —
# recording each ``merged/<repo>`` marker through the mid-run callback, not the
# executor's static per-step ``produces:`` — its "kill between two repos' updates"
# window is a WALL-CLOCK race an external ``kill -9`` of the hub daemon must land
# inside, not a named in-process ``hubnode.*`` registry point. When set to a positive
# number of seconds, the script pauses that long immediately after recording the FIRST
# repo's marker on a multi-repo run, widening that window so the kill is deterministic.
# It fires at most once and never on a crash-recovery re-run (which updates only the
# still-unmarked remainder, so ``pending_count`` is then 1), and is wholly inert unless
# the env var is set — never present in a production land.
_ENV_TEST_PAUSE_AFTER_FIRST_MARKER = "BZ_HUB_LAND_TEST_PAUSE_SECONDS"

_MARKER_PREFIX = "merged/"


def _test_pause_after_first_marker(*, marker_index: int, pending_count: int) -> None:
    """Widen the between-repo-updates window for the mid-script crash sweep — test-only.

    Inert unless :data:`_ENV_TEST_PAUSE_AFTER_FIRST_MARKER` names a positive number of
    seconds. Fires only after the FIRST repo's marker (``marker_index == 1``) on a
    genuinely multi-repo update (``pending_count >= 2``) — so a crash-recovery re-run, which
    updates only the still-unmarked remainder (``pending_count == 1`` for a 2-repo chunk),
    never pauses and the script converges immediately."""
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
    forge_url = os.environ[_ENV_FORGE_URL].rstrip("/")
    token = os.environ.get(_ENV_FORGE_TOKEN)
    owner = os.environ.get(_ENV_FORGE_OWNER, "")
    base_branch = os.environ[_ENV_BASE_BRANCH]
    commits: list[dict[str, str]] = json.loads(os.environ[_ENV_GIT_COMMITS])
    already: set[str] = set(json.loads(os.environ.get(_ENV_ARTIFACT_NAMES, "[]")))
    callback_url = os.environ.get(_ENV_MARKER_CALLBACK_URL, "")

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
                # Crash recovery: a prior run already advanced this ref but the kill hit
                # before its marker became durable. The forge's own fast-forward semantics
                # already treat this as a no-op success (bzh:hub-node-step-idempotence) —
                # no PATCH needed, just (re-)record the marker.
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
