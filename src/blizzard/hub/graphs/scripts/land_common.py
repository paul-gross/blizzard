"""Shared helpers for the land scripts (issue #230) — the one home instead of
:mod:`~blizzard.hub.graphs.scripts.land_default` being the accidental source
:mod:`~blizzard.hub.graphs.scripts.land_ff` and :mod:`~blizzard.hub.graphs.scripts.land_pr_ci`
happened to import from.

Pure stdlib, exactly like the scripts that import from here (``bzh:deterministic-shell``):
``forge_request`` is the one HTTP seam every land script talks to the forge (and the
mid-run marker callback) through; ``qualify_repo``/``pr_title``/``refuse_empty_delivery``
are the small pure helpers their policies share; ``require_env``/``require_json_env`` turn
a missing or malformed injected env var into a named diagnostic instead of a raw traceback;
``marker_recorder`` is the durable-write wrapper around the marker callback POST — a
dropped or unconfirmed marker write used to be silently discarded (the closure's return
value was never even checked), which is exactly how a merge could land with no durable
record of it. A confirmed write (any 2xx, including the idempotent ``recorded: false``
replay) is the only success; anything else is retried a bounded number of times and, if
still unconfirmed, raises :class:`MarkerWriteError` rather than letting the script print
``landed`` over an unrecorded merge.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

# The mid-run marker callback's token header (``X-Blizzard-Marker-Token``, issue #230) is
# a **delivery** credential, injected by the executor alongside the callback URL itself
# (``BZ_HUB_MARKER_TOKEN`` — see ``blizzard.hub.delivery.hub_node.ENV_MARKER_TOKEN``, not
# imported here to keep this package's dependency surface pure stdlib).
_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"
_MARKER_CALLBACK_ENV = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_EXPECT_GIT_COMMITS = "BZ_HUB_EXPECT_GIT_COMMITS"

_MARKER_PREFIX = "merged/"

# GitHub caps PR/issue titles at 256 characters; a resolved feature title longer than
# that is truncated with an ellipsis so PR creation never fails on an over-long title.
_PR_TITLE_MAX = 256

# The marker POST is retried on a connection failure or a 5xx: three attempts total,
# a short fixed backoff between them so a genuinely failing write does not stall the
# node forever, and tests exercising the retry path stay fast.
_MARKER_WRITE_ATTEMPTS = 3
_MARKER_RETRY_BACKOFF_SECONDS = 0.05


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


def refuse_empty_delivery(commits: list[dict[str, str]]) -> None:
    """Exit non-zero when a delivery node is handed nothing to deliver **and the graph
    promised something**.

    "Nothing to deliver" and "everything already delivered" are not the same outcome,
    and every land policy used to answer both with ``landed``: the marker filter below
    yields an empty pending list either way, so a chunk whose ``git_commit`` artifacts
    never materialized printed ``landed`` without contacting the forge at all. That is
    how a fully-built feature reached `done` with no PR, no merge, and a delivery log
    three lines long.

    But an empty set is not always wrong. A non-code chunk — a review, a spike — declares
    no ``git_commit`` anywhere in its graph and still routes through ``deliver`` as the
    uniform terminal (MVP criterion 10): landing nothing is its correct outcome. The two
    are told apart by the graph's own statement of intent, injected as
    ``BZ_HUB_EXPECT_GIT_COMMITS``, not by the emptiness alone — which is the same
    information every caller of this function lacked on its own.

    Absent (an older executor), the var reads as "expected": a delivery policy that fails
    loudly on a set it cannot explain is the safer default of the two.
    """
    if commits:
        return
    if os.environ.get(_ENV_EXPECT_GIT_COMMITS, "1") == "0":
        return  # a non-code chunk: no node ever promised a commit, so none is missing
    print(
        "no git commits to deliver: this chunk submitted no git_commit artifact, so there "
        "is nothing to open a PR for. A delivery node reached with an empty commit set is "
        "a failure, not a landing — check that the nodes before this one declared their "
        "commits (`blizzard runner artifact commit`) and that each verified.",
        file=sys.stderr,
    )
    raise SystemExit(1)


def require_env(name: str) -> str:
    """Read a required, injected environment variable — a missing var exits non-zero
    with a diagnostic naming it, instead of letting a bare ``KeyError`` propagate as a
    traceback the hub-node run-shape never expects (``bzh:hub-node-run-shape``)."""
    value = os.environ.get(name)
    if value is None:
        print(f"missing required environment variable {name}", file=sys.stderr)
        raise SystemExit(1)
    return value


def require_json_env(name: str) -> Any:
    """:func:`require_env` plus ``json.loads`` — malformed JSON exits non-zero with a
    diagnostic naming the offending variable, not a raw ``json.JSONDecodeError``
    traceback."""
    raw = require_env(name)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"malformed JSON in environment variable {name}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def forge_request(
    method: str,
    url: str,
    *,
    token: str | None,
    body: dict[str, Any] | None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """The one HTTP seam every land script (and the marker callback) talks through.

    ``headers`` merges in on top of ``Content-Type`` and the ``Authorization`` header
    ``token`` derives — additive, so a call site that never passes it (every existing
    call before issue #230) is byte-for-byte the request it always sent."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"token {token}")
    if headers:
        for key, value in headers.items():
            req.add_header(key, value)
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


class MarkerWriteError(Exception):
    """Raised when a ``merged/<repo>`` marker write is never confirmed durable — a
    missing callback URL for a repo that needs one, a 4xx, or a 5xx/connection failure
    that keeps failing across every retry. Never swallowed: a script that lets this
    propagate all the way out of its land stage aborts the run instead of printing
    ``landed`` over an unrecorded merge."""


def marker_recorder(
    *,
    callback_url: str,
    token: str,
    request: Callable[..., tuple[int, Any]],
) -> Callable[[str, str], None]:
    """Build the ``record(repo, commit_hash)`` closure a land script calls immediately
    after each repo lands, mid-run (issue #65) — now durable (issue #230): a write that
    is never confirmed raises :class:`MarkerWriteError` instead of being silently
    discarded, and every write carries the run's marker capability token as
    :data:`_MARKER_TOKEN_HEADER`.

    A falsy ``callback_url`` is not fatal by itself — a chunk with nothing pending never
    calls ``record`` at all, and that stays a silent no-op exactly as before. It is fatal
    only once a repo that genuinely needs a marker recorded reaches this closure with
    nowhere to send it.
    """

    def record(repo: str, commit_hash: str) -> None:
        if not callback_url:
            raise MarkerWriteError(
                f"could not record the merge marker for {repo!r}: no {_MARKER_CALLBACK_ENV} was configured for this run"
            )
        headers = {_MARKER_TOKEN_HEADER: token} if token else None
        body = {"name": f"{_MARKER_PREFIX}{repo}", "content": commit_hash}
        last_status: int | None = None
        last_body: Any = None
        for attempt in range(1, _MARKER_WRITE_ATTEMPTS + 1):
            try:
                status, response_body = request("POST", callback_url, token=None, headers=headers, body=body)
            except OSError as exc:
                last_status, last_body = None, str(exc)
                if attempt < _MARKER_WRITE_ATTEMPTS:
                    time.sleep(_MARKER_RETRY_BACKOFF_SECONDS)
                    continue
                raise MarkerWriteError(
                    f"could not record the merge marker for {repo!r}: connection error after "
                    f"{attempt} attempts: {last_body}"
                ) from exc
            if 200 <= status < 300:
                return
            last_status, last_body = status, response_body
            if status >= 500 and attempt < _MARKER_WRITE_ATTEMPTS:
                time.sleep(_MARKER_RETRY_BACKOFF_SECONDS)
                continue
            raise MarkerWriteError(f"could not record the merge marker for {repo!r}: HTTP {last_status} {last_body!r}")

    return record
