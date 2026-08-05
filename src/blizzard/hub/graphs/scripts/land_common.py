"""Shared helpers for the land scripts (issue #230).

Pure stdlib, exactly like the scripts that import from here (``bzh:deterministic-shell``).
``forge_request`` is the one HTTP seam out; the rest are small pure helpers plus the
durable marker-write wrappers, which raise rather than let a script print ``landed`` over
an unrecorded merge."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

# The mid-run marker callback's token header (issue #230) — a **delivery** credential,
# restated rather than imported to keep this package pure stdlib.
_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"
_MARKER_CALLBACK_ENV = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_EXPECT_GIT_COMMITS = "BZ_HUB_EXPECT_GIT_COMMITS"

_MARKER_PREFIX = "merged/"

# GitHub caps PR/issue titles at 256 characters; a resolved feature title longer than
# that is truncated with an ellipsis so PR creation never fails on an over-long title.
_PR_TITLE_MAX = 256

# The marker POST is retried on a connection failure or a 5xx, with a short fixed backoff
# so a genuinely failing write does not stall the node forever.
_MARKER_WRITE_ATTEMPTS = 3
_MARKER_RETRY_BACKOFF_SECONDS = 0.05


def pr_title(feature_title: str, branch: str) -> str:
    """The opened PR's title: the resolved feature title, or the branch name when none
    resolved, truncated to :data:`_PR_TITLE_MAX`."""
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

    Emptiness alone cannot tell a lost ``git_commit`` from a chunk that promised none, so
    ``BZ_HUB_EXPECT_GIT_COMMITS`` decides; absent, it reads as "expected"."""
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
    """Read a required, injected environment variable — a missing var exits non-zero with
    a diagnostic naming it (``bzh:hub-node-run-shape``)."""
    value = os.environ.get(name)
    if value is None:
        print(f"missing required environment variable {name}", file=sys.stderr)
        raise SystemExit(1)
    return value


def require_json_env(name: str) -> Any:
    """:func:`require_env` plus ``json.loads`` — malformed JSON exits non-zero with a
    diagnostic naming the offending variable."""
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
    """The one HTTP seam out of this package.

    ``headers`` merges in on top of ``Content-Type`` and the ``Authorization`` header
    ``token`` derives — additive, so omitting it changes nothing about the request."""
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
    """Raised when a marker write is never confirmed durable — a missing callback URL, a
    4xx, or a 5xx/connection failure surviving every retry. Never swallow it: the run must
    abort rather than print ``landed`` over an unrecorded merge."""


def post_marker(
    *,
    callback_url: str,
    token: str,
    request: Callable[..., tuple[int, Any]],
) -> Callable[[str, str], None]:
    """Build the generic ``post(name, content)`` durable-marker-write closure (issue #232).

    A confirmed write (any 2xx, replays included) is the only success; anything else retries
    a bounded number of times, then raises :class:`MarkerWriteError` — including a falsy
    ``callback_url``, which is fatal only once something actually reaches the closure."""

    def post(name: str, content: str) -> None:
        if not callback_url:
            raise MarkerWriteError(
                f"could not write marker {name!r}: no {_MARKER_CALLBACK_ENV} was configured for this run"
            )
        headers = {_MARKER_TOKEN_HEADER: token} if token else None
        body = {"name": name, "content": content}
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
                    f"could not write marker {name!r}: connection error after {attempt} attempts: {last_body}"
                ) from exc
            if 200 <= status < 300:
                return
            last_status, last_body = status, response_body
            if status >= 500 and attempt < _MARKER_WRITE_ATTEMPTS:
                time.sleep(_MARKER_RETRY_BACKOFF_SECONDS)
                continue
            raise MarkerWriteError(f"could not write marker {name!r}: HTTP {last_status} {last_body!r}")

    return post


def marker_recorder(
    *,
    callback_url: str,
    token: str,
    request: Callable[..., tuple[int, Any]],
) -> Callable[[str, str], None]:
    """:func:`post_marker` specialized to the ``merged/<repo>`` marker name (issues #65,
    #230) — call it immediately after each repo lands, mid-run.

    Every write carries the run's marker capability token as :data:`_MARKER_TOKEN_HEADER`;
    retry and failure behavior are :func:`post_marker`'s."""
    post = post_marker(callback_url=callback_url, token=token, request=request)

    def record(repo: str, commit_hash: str) -> None:
        post(f"{_MARKER_PREFIX}{repo}", commit_hash)

    return record
