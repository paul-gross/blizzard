"""The GitHub-shaped PM work-source binding (``bzh:pluggable-seams``).

Implements :class:`~blizzard.hub.pm.source.IPmSource` against a GitHub REST v3
surface — the ``blizzard-mock`` forge in tests, GitHub in production. Confined to
``internal/`` (adapter placement, ``bzh:dependency-inversion``); ``httpx`` is used only
here. One instance per configured ``[[pm_source]]`` (D-108): pinned to its own
``repo``, its own ``web_base`` (an origin, e.g. ``https://github.com``), and carrying
its own credentialed client — never the delivery forge's.

D-107 gives the pointer its own ``source`` name and an opaque ``ref`` (this binding's
own item token — a GitHub issue number): ``fetch``/``label``/``web_url`` trust
``pointer.ref`` directly rather than re-deriving it from a URL, unlike the Phase 1/2
shape this binding grew from (``pm/label.py``'s issue-URL grammar, now gone).

D-111 gives ``parse`` its production caller and, with it, the URL grammar `cli.py`'s
own ``_ISSUE_URL_RE`` used to carry (a config-blind guess that a source's name equals
its repo tail): the same regex now lives here, checked against *this binding's own
configured* ``repo`` — the hub's own configuration, not a client-side heuristic.
"""

from __future__ import annotations

import re

import httpx

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.source import IPmSource, PmItem, PmSourceError

_log = get_logger("blizzard.hub.pm")

# A GitHub-shaped issue reference — {owner}/{repo}/issues/{number} — with or without a
# leading scheme://host and with or without the REST /repos/ prefix, so both the full
# browser URL and the schemeless shorthand resolve the same way (D-111).
_ISSUE_URL_RE = re.compile(r"(?:^|/)(?:repos/)?(?P<owner>[^/:#]+)/(?P<repo>[^/:#]+)/issues/(?P<number>\d+)/?$")


class GitHubPmSource:
    """Vendor-native issue reader over a GitHub-shaped forge, pinned to one repo."""

    def __init__(self, client: httpx.Client, *, name: str, repo: str, web_base: str) -> None:
        self._client = client
        self._name = name
        self._repo = repo
        self._web_base = web_base.rstrip("/")

    def parse(self, token: str) -> PmPointer | None:
        """This source's own ingest-token forms (D-107/D-111) into a pointer, or
        ``None`` when ``token`` isn't shaped for this source: ``{name}:{number}``,
        ``{name}#{number}``, or the item's own issue URL — full
        (``https://github.com/{owner}/{repo}/issues/{n}``) or schemeless
        (``{owner}/{repo}/issues/{n}``) — naming *this binding's own configured*
        ``repo``. A URL naming a different repo is not this source's token; some other
        configured binding may claim it instead."""
        for sep_char in (":", "#"):
            prefix, sep, ref = token.partition(sep_char)
            if sep and prefix == self._name and ref.isdigit():
                return PmPointer(source=self._name, ref=ref)
        match = _ISSUE_URL_RE.search(token)
        if match is not None and f"{match['owner']}/{match['repo']}" == self._repo:
            return PmPointer(source=self._name, ref=match["number"])
        return None

    def fetch(self, pointer: PmPointer) -> PmItem:
        base = f"/repos/{self._repo}/issues/{pointer.ref}"
        try:
            issue = self._client.get(base)
            issue.raise_for_status()
            comments = self._client.get(f"{base}/comments")
            comments.raise_for_status()
        except httpx.HTTPError as exc:
            _log.error("pm-item fetch failed", source=pointer.source, ref=pointer.ref, error=str(exc))
            raise PmSourceError(f"failed to read {self._name}#{pointer.ref}: {exc}") from exc
        return PmItem(
            body=str(issue.json().get("body") or ""),
            title=str(issue.json().get("title") or ""),
            comments=[str(c.get("body") or "") for c in comments.json()],
        )

    def label(self, pointer: PmPointer) -> str | None:
        """``{name}#{ref}`` (D-110) — always renders; ``ref`` is opaque here (D-107)."""
        return f"{self._name}#{pointer.ref}"

    def web_url(self, pointer: PmPointer) -> str | None:
        return f"{self._web_base}/{self._repo}/issues/{pointer.ref}"

    def branch_url(self, repo: str, branch_name: str) -> str | None:
        """The forge ``tree`` URL for ``branch_name`` on ``repo`` — an owner-less repo (a
        produced artifact names its repo by the worktree dir alone) is qualified with this
        source's own repo's owner; an already ``owner/name`` repo passes through."""
        repo_path = repo if "/" in repo else f"{self._owner}/{repo}"
        return f"{self._web_base}/{repo_path}/tree/{branch_name}"

    @property
    def _owner(self) -> str:
        return self._repo.split("/", 1)[0]


def _conforms_pm_source(x: GitHubPmSource) -> IPmSource:
    return x
