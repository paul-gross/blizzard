"""The GitHub-shaped PM work-source binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.pm.source.IPmSource` against a GitHub REST v3
surface — the ``blizzard-mock`` forge in tests, GitHub in production. Confined to
``internal/`` (adapter placement, ``bzh:dependency-inversion``); ``httpx`` is used
only here.

The hub derives the API calls from the pointer's URL and its **own** configured
base URL + token (D-047/D-084): the ``{owner}/{repo}/{number}`` triple is parsed
from the pointer and re-issued as ``/repos/{owner}/{repo}/issues/{number}`` (plus
``/comments``) against the injected client, so the hub reads with its credentials,
never a runner's. The client is wired at the composition root with the forge base
URL and auth header; tests inject a client bound to a fake GitHub-shaped app.
"""

from __future__ import annotations

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.label import parse_issue_url
from blizzard.hub.pm.source import IPmSource, PmItem, PmSourceError

_log = get_logger("blizzard.hub.pm")


class GitHubPmSource:
    """Vendor-native issue reader over a GitHub-shaped forge."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def fetch(self, pointer: PmPointer) -> PmItem:
        owner, repo, number = _parse_issue(pointer.url)
        base = f"/repos/{owner}/{repo}/issues/{number}"
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


def _parse_issue(url: str) -> tuple[str, str, int]:
    ref = parse_issue_url(url)  # the shared issue-URL parse (pm/label.py, D-075)
    if ref is None:
        raise PmSourceError(f"pointer URL is not a GitHub-shaped issue: {url}")
    return ref.owner, ref.repo, ref.number


def _conforms_pm_source(x: GitHubPmSource) -> IPmSource:
    return x
