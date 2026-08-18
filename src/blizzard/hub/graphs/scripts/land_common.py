"""What every land script is built from (issue #230).

Pure stdlib, exactly like the scripts that import from here (``bzh:deterministic-shell``).
:class:`LandRun` is one node visit — its :class:`ScriptEnv` and its marker channel, which
raises rather than let a script print ``landed`` over an unrecorded merge; :class:`PullRequest`
is one repo's PR within that visit, and the sole owner of the forge's ``pulls`` routes."""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

_HUB_USER = "blizzard-hub"

# The mid-run marker callback's token header (issue #230) — a **delivery** credential,
# restated rather than imported to keep this package pure stdlib.
_MARKER_TOKEN_HEADER = "X-Blizzard-Marker-Token"
_ENV_EXPECT_GIT_COMMITS = "BZ_HUB_EXPECT_GIT_COMMITS"

_MARKER_PREFIX = "merged/"

_ENV_FORGE_URL = "BZ_FORGE_URL"
_ENV_FORGE_TOKEN = "BZ_FORGE_TOKEN"
_ENV_FORGE_OWNER = "BZ_FORGE_OWNER"
_ENV_BASE_BRANCH = "BZ_HUB_BASE_BRANCH"
_ENV_GIT_COMMITS = "BZ_HUB_GIT_COMMITS"
_ENV_ARTIFACT_NAMES = "BZ_HUB_ARTIFACT_NAMES"
_ENV_MARKER_CALLBACK_URL = "BZ_HUB_MARKER_CALLBACK_URL"
_ENV_MARKER_TOKEN = "BZ_HUB_MARKER_TOKEN"
_ENV_FEATURE_TITLE = "BZ_HUB_FEATURE_TITLE"

# Test-only instrumentation for the mid-script crash sweep: the between-repo window is a
# wall-clock race a `kill -9` must land inside, so a positive value widens it.
_ENV_TEST_PAUSE_AFTER_FIRST_MARKER = "BZ_HUB_LAND_TEST_PAUSE_SECONDS"

# GitHub caps PR/issue titles at 256 characters; a resolved feature title longer than
# that is truncated with an ellipsis so PR creation never fails on an over-long title.
_PR_TITLE_MAX = 256

# The marker POST is retried on a connection failure or a 5xx, with a short fixed backoff
# so a genuinely failing write does not stall the node forever.
_MARKER_WRITE_ATTEMPTS = 3
_MARKER_RETRY_BACKOFF_SECONDS = 0.05


@dataclass(frozen=True)
class ScriptEnv:
    """The env a hub node injects (``bzh:hub-node-env-contract``) — a land script's only
    channel in. A required var that is missing, or malformed JSON, exits non-zero with a
    diagnostic naming it (``bzh:hub-node-run-shape``)."""

    environ: Mapping[str, str] = field(default_factory=lambda: os.environ)

    def get(self, name: str, default: str = "") -> str:
        return self.environ.get(name, default)

    def require(self, name: str) -> str:
        value = self.environ.get(name)
        if value is None:
            print(f"missing required environment variable {name}", file=sys.stderr)
            raise SystemExit(1)
        return value

    def require_json(self, name: str) -> Any:
        raw = self.require(name)
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
    4xx, or a 5xx/connection failure surviving every retry. Catch it at a script's top
    level, never in its per-repo loop: the run must abort rather than print ``landed``
    over an unrecorded merge, and must not carry on to the next repo."""


@dataclass(frozen=True)
class MarkerWriter:
    """The run's durable marker channel (issues #65, #230, #232), carrying its capability
    token as :data:`_MARKER_TOKEN_HEADER`. Constructing one reaches nothing — every failure
    mode, a missing ``callback_url`` included, surfaces on the write."""

    callback_url: str
    token: str
    request: Callable[..., tuple[int, Any]]

    def post(self, name: str, content: str) -> None:
        """Write marker ``name``, or raise. A confirmed 2xx (replays included) is the only
        success; a connection error or 5xx retries a bounded number of times first."""
        if not self.callback_url:
            raise MarkerWriteError(
                f"could not write marker {name!r}: no {_ENV_MARKER_CALLBACK_URL} was configured for this run"
            )
        headers = {_MARKER_TOKEN_HEADER: self.token} if self.token else None
        body = {"name": name, "content": content}
        last_status: int | None = None
        last_body: Any = None
        for attempt in range(1, _MARKER_WRITE_ATTEMPTS + 1):
            try:
                status, response_body = self.request("POST", self.callback_url, token=None, headers=headers, body=body)
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

    def record(self, repo: str, commit_hash: str) -> None:
        """Mark ``repo`` landed — call immediately after it does, mid-run."""
        self.post(f"{_MARKER_PREFIX}{repo}", commit_hash)


@dataclass(frozen=True)
class LandRun:
    """One ``deliver`` node visit: the env the executor injected, and the two channels out
    of it — the forge and :attr:`markers`."""

    forge_url: str
    base_branch: str
    commits: list[dict[str, str]]
    already: set[str]
    markers: MarkerWriter
    owner: str = ""
    token: str | None = None
    feature_title: str = ""
    #: The HTTP seam, resolved per call so :func:`forge_request` stays substitutable.
    request: Callable[..., tuple[int, Any]] | None = None
    env: ScriptEnv = field(default_factory=ScriptEnv)

    @classmethod
    def from_env(cls, env: ScriptEnv | None = None) -> LandRun:
        """Read the whole injected env (``bzh:hub-node-env-contract``); a missing required
        var exits non-zero naming it, before anything reaches the forge."""
        env = env or ScriptEnv()
        # Required vars are read in table order, so the first one missing is the one named.
        forge_url = env.require(_ENV_FORGE_URL).rstrip("/")
        base_branch = env.require(_ENV_BASE_BRANCH)
        commits: list[dict[str, str]] = env.require_json(_ENV_GIT_COMMITS)
        return cls(
            forge_url=forge_url,
            base_branch=base_branch,
            commits=commits,
            already=set(json.loads(env.get(_ENV_ARTIFACT_NAMES, "[]"))),
            markers=MarkerWriter(
                callback_url=env.get(_ENV_MARKER_CALLBACK_URL),
                token=env.get(_ENV_MARKER_TOKEN),
                request=forge_request,
            ),
            owner=env.get(_ENV_FORGE_OWNER),
            token=env.get(_ENV_FORGE_TOKEN),
            feature_title=env.get(_ENV_FEATURE_TITLE),
            env=env,
        )

    def api(self, method: str, path: str, body: dict[str, Any] | None = None) -> tuple[int, Any]:
        """One forge call, ``path`` relative to the injected forge URL."""
        request = self.request or forge_request
        return request(method, f"{self.forge_url}{path}", token=self.token, body=body)

    def repo(self, bare_repo: str) -> str:
        """``bare_repo`` as the ``owner/name`` a forge route resolves."""
        if "/" in bare_repo or not self.owner:
            return bare_repo
        return f"{self.owner}/{bare_repo}"

    def pr_title(self, branch: str) -> str:
        """A newly opened PR's title: :attr:`feature_title`, or ``branch`` when none
        resolved, truncated to :data:`_PR_TITLE_MAX`."""
        title = self.feature_title or branch
        if len(title) > _PR_TITLE_MAX:
            title = title[: _PR_TITLE_MAX - 1].rstrip() + "…"
        return title

    def contains(self, bare_repo: str, ref: str) -> bool:
        """Whether ``bare_repo``'s base branch already holds every commit on ``ref``.

        Read from the forge's own comparison, never from the text of a refusal:
        ``identical``/``behind`` both mean ``ref`` adds nothing. Any unreadable answer is
        ``False`` — a degraded read is never "there was nothing to land"."""
        repo = self.repo(bare_repo)
        try:
            status, payload = self.api("GET", f"/repos/{repo}/compare/{self.base_branch}...{ref}")
        except Exception:
            return False
        if status != 200 or not isinstance(payload, dict):
            return False
        return payload.get("status") in {"identical", "behind"}

    def pending(self) -> list[dict[str, str]]:
        """The repos still to land — those with no ``merged/<repo>`` marker yet.

        Emptiness alone cannot tell a lost ``git_commit`` from a chunk that promised none,
        so :data:`_ENV_EXPECT_GIT_COMMITS` decides; absent, it reads as "expected" and an
        empty commit set exits non-zero."""
        if not self.commits and self.env.get(_ENV_EXPECT_GIT_COMMITS, "1") != "0":
            print(
                "no git commits to deliver: this chunk submitted no git_commit artifact, so there "
                "is nothing to open a PR for. A delivery node reached with an empty commit set is "
                "a failure, not a landing — check that the nodes before this one declared their "
                "commits (`blizzard runner artifact commit`) and that each verified.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        return [c for c in self.commits if f"{_MARKER_PREFIX}{c['repo']}" not in self.already]

    def pause_for_crash_window(self, *, marker_index: int, pending_count: int) -> None:
        """Widen the between-repo window for the mid-script crash sweep — test-only.

        Inert unless :data:`_ENV_TEST_PAUSE_AFTER_FIRST_MARKER` names a positive number of
        seconds, and fires only after the FIRST marker of a genuinely multi-repo run, so a
        crash-recovery re-run never pauses."""
        raw = self.env.get(_ENV_TEST_PAUSE_AFTER_FIRST_MARKER)
        if not raw or marker_index != 1 or pending_count < 2:
            return
        seconds = float(raw)
        if seconds > 0:
            print(f"[test] pausing {seconds}s after the first marker to widen the crash window", file=sys.stderr)
            time.sleep(seconds)


class PullRequestOpenError(Exception):
    """Raised when the forge refused to open the chunk's PR — whether that is a conflict
    or merely worth another poll is the script's call."""


class NothingToLand(Exception):
    """Raised when a repo's branch adds no commit its base branch lacks — a **no-op
    landing**, not a failure: no PR can be opened and no poll changes that, so a script
    records the repo's ``merged/<repo>`` marker and moves on, as ``land_ff`` does for an
    already-advanced base ref (``bzh:hub-node-step-idempotence``)."""


class MergeDidNotLand(Exception):
    """Raised when a merge did not land and a re-read confirms the PR is still open —
    :attr:`result` is the forge's refusal, for the script's own diagnostic."""

    def __init__(self, result: Any) -> None:
        super().__init__(str(result))
        self.result = result


@dataclass(frozen=True)
class PullRequest:
    """One repo's PR for a chunk's branch, and the sole owner of the forge's ``pulls``
    routes. :attr:`body` is the PR as of the last read — :meth:`of` and :meth:`reread`
    are the only two things that refresh it."""

    run: LandRun
    bare_repo: str
    number: int
    body: dict[str, Any]

    @classmethod
    def of(cls, run: LandRun, commit: dict[str, str]) -> PullRequest:
        """The open PR for ``commit``'s branch — opening one first when none exists — then
        read live. Raises :class:`PullRequestOpenError` when the forge refuses to open."""
        repo = run.repo(commit["repo"])
        branch = commit["branch"]
        _, listed = run.api("GET", f"/repos/{repo}/pulls?state=open")
        existing = next((p for p in (listed or []) if p.get("head", {}).get("ref") == branch), None)
        if existing is None:
            status, created = run.api(
                "POST",
                f"/repos/{repo}/pulls",
                {
                    "title": run.pr_title(branch),
                    "head": branch,
                    "base": run.base_branch,
                    "user": _HUB_USER,
                },
            )
            if status != 201:
                if run.contains(commit["repo"], branch):
                    raise NothingToLand(f"{repo}:{branch} adds no commit {run.base_branch} does not already have")
                raise PullRequestOpenError(f"could not open a PR for {repo}:{branch}: {created}")
            existing = created
        return cls(run, commit["repo"], int(existing["number"]), {}).reread()

    def __str__(self) -> str:
        return f"{self.repo}#{self.number}"

    @property
    def repo(self) -> str:
        return self.run.repo(self.bare_repo)

    @property
    def merged(self) -> bool:
        return bool(self.body.get("merged"))

    @property
    def mergeable_state(self) -> str | None:
        return self.body.get("mergeable_state")

    @property
    def head_sha(self) -> str | None:
        return (self.body.get("head") or {}).get("sha")

    @property
    def url(self) -> str:
        return self.body.get("html_url") or ""

    def reread(self) -> PullRequest:
        _, pull = self.run.api("GET", f"/repos/{self.repo}/pulls/{self.number}")
        return replace(self, body=pull or {})

    def update_branch(self, expected_head_sha: str) -> tuple[int, str]:
        """Ask the forge to merge the base branch in — guarded on ``expected_head_sha``,
        so updates never stack."""
        status, body = self.run.api(
            "PUT",
            f"/repos/{self.repo}/pulls/{self.number}/update-branch",
            {"expected_head_sha": expected_head_sha},
        )
        return status, ((body or {}).get("message", "") if isinstance(body, dict) else "")

    def merge(self, sha: str) -> str:
        """Merge at ``sha`` and return the landed commit.

        An already-merged PR is a prior run's un-marked merge, a no-op to redo
        (``bzh:hub-node-step-idempotence``); anything else raises :class:`MergeDidNotLand`."""
        status, result = self.run.api(
            "PUT",
            f"/repos/{self.repo}/pulls/{self.number}/merge",
            {
                "commit_message": self.run.feature_title or f"blizzard: land {self.bare_repo}",
                "sha": sha,
                "merge_method": "merge",
                "user": _HUB_USER,
            },
        )
        if status == 200 and (result or {}).get("merged"):
            return (result or {}).get("sha") or sha
        landed = self.reread()
        if not landed.merged:
            raise MergeDidNotLand(result)
        return landed.body.get("merge_commit_sha") or sha
