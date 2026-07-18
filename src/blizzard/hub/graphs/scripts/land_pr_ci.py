"""The PR + CI-watch example graph's `deliver` node script.

The alternative delivery policy `hub/graphs/delivery-pr-ci.yaml` ships as an EXAMPLE
alongside the default graph, proving delivery policy lives in YAML. It honors the same
hub-command-node authoring contract as the default land script
(``blizzard-harness:/standards/hub-nodes.md``): instead of the default graph's
straight-through check-all/push-all
(:mod:`blizzard.hub.graphs.scripts.land_default`), this opens a PR per repo and
reports the reserved ``pending`` outcome (``bzh:hub-node-outcome-protocol``) while any
PR is not yet cleanly mergeable, letting the fleet-wide hub-execution slot free between
polls so other chunks' hub nodes run in the gap. Once every repo's PR reads
``mergeable_state == "clean"``, it merges each and records a ``merged/<repo>``
marker, then prints ``landed``. Exceeding the node's own ``poll_timeout`` is the
executor's job (``bzh:hub-node-outcome-protocol``) — this script never times anything
out itself.

Same env contract as :mod:`~blizzard.hub.graphs.scripts.land_default`
(``BZ_FORGE_URL``/``BZ_FORGE_TOKEN``/``BZ_FORGE_OWNER``/``BZ_HUB_BASE_BRANCH``/
``BZ_HUB_GIT_COMMITS``/``BZ_HUB_ARTIFACT_NAMES``/``BZ_HUB_MARKER_CALLBACK_URL``); exit
code is always 0, the authored choice (``landed`` or the reserved ``pending``) is the
last stdout line.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

from blizzard.hub.graphs.scripts.land_default import forge_request, qualify_repo

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"

_HUB_USER = "blizzard-hub"
_MARKER_PREFIX = "merged/"
_PENDING = "pending"


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

    pulls: list[tuple[str, str, int, str, dict[str, Any]]] = []  # (bare, qualified, number, commit, pull)
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
                {"title": f"blizzard: land {branch}", "head": branch, "base": base_branch, "user": _HUB_USER},
            )
            if status != 201:
                print(f"could not open a PR for {repo}:{branch}: {created}", file=sys.stderr)
                print(_PENDING)  # a transient forge hiccup is worth another poll, not a bounce
                return 0
            existing = created
        number = int(existing["number"])
        _, pull = api("GET", f"/repos/{repo}/pulls/{number}")
        pulls.append((bare_repo, repo, number, commit["commit"], pull))

    if any(not p[4].get("merged") and p[4].get("mergeable_state") != "clean" for p in pulls):
        print("not every PR is clean yet — waiting for CI", file=sys.stderr)
        print(_PENDING)
        return 0

    for bare_repo, repo, number, commit_hash, pull in pulls:
        if pull.get("merged"):
            record_marker(bare_repo, pull.get("merge_commit_sha") or commit_hash)
            continue
        status, result = api(
            "PUT",
            f"/repos/{repo}/pulls/{number}/merge",
            {
                "commit_message": f"blizzard: land {bare_repo}",
                "sha": commit_hash,
                "merge_method": "merge",
                "user": _HUB_USER,
            },
        )
        if status != 200 or not (result or {}).get("merged"):
            print(f"merge of {repo}#{number} failed: {result}", file=sys.stderr)
            print(_PENDING)  # poll again rather than bounce on a transient merge race
            return 0
        record_marker(bare_repo, result.get("sha") or commit_hash)

    print("landed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
