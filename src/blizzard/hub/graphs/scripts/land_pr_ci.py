"""The PR + CI-watch delivery policy's `deliver` node script — self-healing.

This alternative delivery policy backs the `advanced-development-workflow` graph's
`deliver` node (`hub/graphs/advanced-development-workflow/graph.yaml`), proving delivery
policy lives in YAML. It honors the same hub-command-node authoring contract as the default land script
(``blizzard-harness:/standards/hub-nodes.md``): pure stdlib against the forge, env
injected by the executor, the authored choice (``landed``/``conflict``) or the reserved
``pending`` printed as the LAST stdout line, diagnostics to stderr, exit 0 always.

Unlike the default graph's strict one-shot (:mod:`blizzard.hub.graphs.scripts.land_default`,
which bounces to ``build`` on *any* non-``clean`` state), this opens a PR per repo and
routes by the PR's live ``mergeable_state`` — resolving what is mechanical or transient
without ever waking the LLM:

    clean               -> merge it
    unknown             -> "pending"  (GitHub still computing mergeability — re-poll)
    behind              -> PUT .../update-branch, then "pending"  (base moved, no conflict — self-heal)
    blocked/unstable    -> "pending"  (required CI/reviews not green yet — WAIT, the CI-watch case)
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

This improves on the prior CI-watch policy in two ways: a real ``dirty`` conflict bounces
*immediately* instead of waiting out the full ``poll_timeout``, and a ``behind`` branch is
*healed* via ``update-branch`` instead of pending forever (nothing used to update it).

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

from blizzard.hub.graphs.scripts.land_default import forge_request, pr_title, qualify_repo

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_FEATURE_TITLE = "BZ_HUB_FEATURE_TITLE"

_HUB_USER = "blizzard-hub"
_MARKER_PREFIX = "merged/"

# The reserved + authored outcomes this script prints as its last stdout line.
_LANDED = "landed"
_CONFLICT = "conflict"
_PENDING = "pending"

# Pure routing decisions (what to do with one repo after reading its live PR).
_PUSH = "push"  # clean (or already merged) — eligible for the merge stage
_WAIT = "wait"  # unknown / required-checks-not-green / … — re-poll, no side effect
_UPDATE = "update"  # behind — fire update-branch, then re-poll
_BOUNCE = "bounce"  # dirty — a real content conflict, kick back to build


def classify(mergeable_state: str | None, *, merged: bool) -> str:
    """Map a PR's live ``(merged, mergeable_state)`` to one routing decision — pure.

    The whole risk of this script lives here, so it is a network-free function the
    ``--selftest`` mode asserts against. ``clean``/already-merged -> push; ``dirty`` ->
    bounce (the only true LLM kick-back); ``behind`` -> update-branch then wait;
    everything else (``unknown``, ``blocked``, ``unstable``, ``has_hooks``, ``draft``,
    missing) -> wait, because none is a content conflict — the CI-watch case (``blocked``/
    ``unstable``) is exactly a wait — and the node's ``poll_timeout`` is the backstop."""
    if merged:
        return _PUSH
    if mergeable_state == "clean":
        return _PUSH
    if mergeable_state == "dirty":
        return _BOUNCE
    if mergeable_state == "behind":
        return _UPDATE
    return _WAIT


class _Conflict(Exception):
    """Raised to abort the check stage as a real conflict — nothing has been merged."""


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
        print(_LANDED)
        return 0

    # --- check stage: read every pending repo's live PR state; decide per repo. No repo
    #     is merged unless ALL check `clean` (chunk atomicity). A `dirty` short-circuits
    #     to `conflict`; a `behind` self-heals via update-branch; unknown/CI-not-green wait.
    to_merge: list[tuple[str, str, int, str]] = []  # (bare repo, qualified repo, pr number, head sha)
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
                print(f"{repo}#{number} is {state} — not cleanly mergeable yet; re-polling", file=sys.stderr)
                wait = True
                continue
            # decision == _PUSH: clean (or already merged) — eligible.
            to_merge.append((bare_repo, repo, number, head_sha))
    except _Conflict as exc:
        print(f"conflict: {exc}", file=sys.stderr)
        print(_CONFLICT)
        return 0

    if wait:
        # At least one repo is not cleanly mergeable yet (unknown/behind/CI-not-green) —
        # release the hub slot and re-poll; merge NOTHING (chunk atomicity).
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
    return 1 if failures else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv[1:]:
        sys.exit(_selftest())
    sys.exit(main())
