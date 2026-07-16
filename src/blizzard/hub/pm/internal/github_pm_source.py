"""The GitHub-shaped PM work-source binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.pm.source.IPmSource` against a GitHub REST v3
surface — the ``blizzard-mock`` forge in tests, GitHub in production. Confined to
``internal/`` (adapter placement, ``bzh:dependency-inversion``); ``httpx`` is used only
here. One instance per configured ``[[pm_source]]`` (D-106): pinned to its own
``repo``, its own ``web_base`` (an origin, e.g. ``https://github.com``), and carrying
its own credentialed client — never the delivery forge's.

Owns the GitHub issue-URL grammar absorbed from ``pm/label.py`` (D-108): the issue-URL
regex is this binding's own copy now, decoupling it from the domain-layer module
``pm/label.py`` still holds for its surviving ``forge_web_base`` sniff (retired only in
Phase 3, once the pointer carries ``source`` explicitly and this binding's own
``web_url``/``branch_url`` take over).

This phase (D-105 not yet landed) the pointer is still ``{provider, url}``: ``fetch``
still extracts the issue *number* from ``pointer.url``, but the repo comes from this
binding's own configuration — a pointer whose URL names a different repo than this
source is configured for is a :class:`~blizzard.hub.pm.source.PmSourceError`, not a
silent cross-repo read. ``owns`` (D-107) answers the same repo-match as a boolean,
without raising — the ingest-time resolver and the board label/fetch both use it to
find which configured source is a pointer's binding.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.source import IPmSource, PmItem, PmSourceError, UnknownSource

_log = get_logger("blizzard.hub.pm")

# The GitHub-shaped issue reference — an {owner}/{repo}/{number} triple, with or
# without the REST ``/repos/`` prefix, and with or without a leading scheme://host (a
# bare ``owner/repo/issues/N`` ingest shorthand parses too — D-107's resolver needs it
# to match the schemeless form the CLI/tests still ingest). Absorbed from
# ``pm/label.py`` (D-108): this binding owns its own copy rather than importing the
# domain module's.
_ISSUE_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")


def _repo_of(url: str) -> str | None:
    """The ``owner/repo`` a GitHub-shaped URL names, or ``None`` when it has fewer than
    two path segments — repo membership alone (D-107), independent of whether the URL is
    issue-shaped. ``owns`` needs this: a pointer at a non-issue path (a wiki page, say)
    still belongs to its repo's configured source, even though ``label``/``web_url``
    (which need the issue grammar specifically) render it ``None``. Tolerates the same
    schemeless shorthand as ``_ISSUE_RE`` and an optional REST ``/repos/`` prefix."""
    path = urlsplit(url).path.strip("/") or url.strip("/")
    segments = [s for s in path.split("/") if s]
    if segments and segments[0] == "repos":
        segments = segments[1:]
    if len(segments) < 2:
        return None
    return f"{segments[0]}/{segments[1]}"


class GitHubPmSource:
    """Vendor-native issue reader over a GitHub-shaped forge, pinned to one repo."""

    def __init__(self, client: httpx.Client, *, name: str, repo: str, web_base: str) -> None:
        self._client = client
        self._name = name
        self._repo = repo
        self._web_base = web_base.rstrip("/")

    def parse(self, token: str) -> PmPointer:
        """A ``{name}:{number}`` ingest token (D-105) into a pointer pinned to this repo."""
        prefix, sep, ref = token.partition(":")
        if not sep or prefix != self._name or not ref.isdigit():
            raise UnknownSource(f"{token!r} is not a {self._name!r} source token")
        url = f"{self._web_base}/{self._repo}/issues/{ref}"
        return PmPointer(provider="github", url=url)

    def fetch(self, pointer: PmPointer) -> PmItem:
        number = self._issue_number(pointer.url)
        base = f"/repos/{self._repo}/issues/{number}"
        try:
            issue = self._client.get(base)
            issue.raise_for_status()
            comments = self._client.get(f"{base}/comments")
            comments.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("pm-item fetch failed", url=pointer.url, error=str(exc))
            raise PmSourceError(f"failed to read {pointer.url}: {exc}") from exc
        return PmItem(
            body=str(issue.json().get("body") or ""),
            comments=[str(c.get("body") or "") for c in comments.json()],
        )

    def label(self, pointer: PmPointer) -> str | None:
        """``{name}#{number}`` (D-108) — ``None`` when the URL isn't issue-shaped."""
        match = _ISSUE_RE.search(pointer.url)
        if match is None:
            return None
        return f"{self._name}#{match['number']}"

    def web_url(self, pointer: PmPointer) -> str | None:
        match = _ISSUE_RE.search(pointer.url)
        if match is None:
            return None
        return f"{self._web_base}/{self._repo}/issues/{match['number']}"

    def owns(self, pointer: PmPointer) -> bool:
        """True when ``pointer``'s URL names this source's own configured repo (D-107) —
        repo membership alone, not the stricter issue-shape ``label``/``fetch`` need."""
        return _repo_of(pointer.url) == self._repo

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The forge ``tree`` URL for ``branch_name`` on ``repo`` — an owner-less repo (a
        produced artifact names its repo by the worktree dir alone) is qualified with this
        source's own repo's owner; an already ``owner/name`` repo passes through."""
        repo_path = repo if "/" in repo else f"{self._owner}/{repo}"
        return f"{self._web_base}/{repo_path}/tree/{branch_name}"

    @property
    def _owner(self) -> str:
        return self._repo.split("/", 1)[0]

    def _issue_number(self, url: str) -> int:
        match = _ISSUE_RE.search(url)
        if match is None:
            raise PmSourceError(f"pointer URL is not a GitHub-shaped issue: {url}")
        owner, repo = match["owner"], match["repo"]
        if f"{owner}/{repo}" != self._repo:
            raise PmSourceError(f"pointer names {owner}/{repo}, but this source is configured for {self._repo}")
        return int(match["number"])


def _conforms_pm_source(x: GitHubPmSource) -> IPmSource:
    return x
