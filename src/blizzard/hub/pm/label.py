"""The forge web base (D-075) — the one browser-openable origin a chunk's pointers share.

Historically this module also derived the board's pointer label (``gh:blizzard#8``);
D-107 moves that rendering, and the GitHub issue-URL grammar it depended on, onto the
configured :class:`~blizzard.hub.pm.source.IPmSource` binding
(``pm/internal/github_pm_source.py``) — provider grammar is adapter knowledge, not a
domain-layer concern once there is more than one provider. What survives here is
``forge_web_base``: the pointer's URL is still (this phase) the only browser-openable
forge address the hub holds, so a chunk's artifact branch links still sniff it from
the first issue-shaped pointer. This retires in Phase 3, once the pointer carries
``source`` explicitly and ``IPmSource.branch_url`` takes over. Dependency-free
(``bzh:domain-core``) — pure parsing over the domain pointer, no transport or store.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

# The GitHub-shaped issue reference — an {owner}/{repo}/{number} triple, with or
# without the REST ``/repos/`` prefix. Also owned (its own copy) by the GitHub PM
# adapter now (D-107) — this module no longer feeds it.
_ISSUE_RE = re.compile(r"/(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")


@dataclass(frozen=True)
class IssueRef:
    """The ``{owner}/{repo}/{number}`` triple an issue-shaped pointer URL carries."""

    owner: str
    repo: str
    number: int


def parse_issue_url(url: str) -> IssueRef | None:
    """The issue triple in ``url``, or ``None`` when it is not issue-shaped."""
    match = _ISSUE_RE.search(url)
    if match is None:
        return None
    return IssueRef(owner=match["owner"], repo=match["repo"], number=int(match["number"]))


@dataclass(frozen=True)
class ForgeWebBase:
    """A forge's browser-facing base — ``{scheme}://{host}`` and the owner segment.

    Derived from an issue-shaped PM pointer URL (the one browser-openable forge address
    the hub holds); the delivery forge base (``BZ_FORGE_URL``) is an API base, not a web
    ``tree`` base, so the pointer is the reliable web origin."""

    origin: str
    owner: str

    def branch_url(self, repo: str, branch_name: str) -> str:
        """The forge ``tree`` URL for ``branch_name`` on ``repo`` — an owner-less repo (a
        produced artifact names its repo by the worktree dir alone) is qualified with the
        pointer's owner; an already ``owner/name`` repo passes through."""
        repo_path = repo if "/" in repo else f"{self.owner}/{repo}"
        return f"{self.origin}/{repo_path}/tree/{branch_name}"


def forge_web_base(pointer_urls: Iterable[str]) -> ForgeWebBase | None:
    """The forge web base from the first issue-shaped pointer, or ``None`` when none is.

    A chunk's pointers share one forge, so any issue-shaped URL fixes the ``{scheme}://
    {host}`` origin and the owner the board links branches under (D-075)."""
    for url in pointer_urls:
        ref = parse_issue_url(url)
        if ref is None:
            continue
        parts = urlsplit(url)
        if not parts.scheme or not parts.netloc:
            continue
        return ForgeWebBase(origin=f"{parts.scheme}://{parts.netloc}", owner=ref.owner)
    return None
