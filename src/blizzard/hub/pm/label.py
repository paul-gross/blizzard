"""The board-legible PM pointer label (D-075) — ``{provider-code}:{repo}#{number}``.

D-075 wants the PM pointer legible on the board without reassembly: the raw
``{provider, url}`` pair is the durable referent, and this module derives the human
form the views render — ``gh:blizzard#8``. The issue-URL parse is shared with the
GitHub adapter (one regex, not one per consumer), and the provider short-code map is
the single provider→indicator registry: a provider without an entry renders its raw
tag rather than an invented code. Dependency-free (``bzh:domain-core``) — pure
parsing over the domain pointer, no transport or store.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from blizzard.hub.domain.work import PmPointer

# The GitHub-shaped issue reference — an {owner}/{repo}/{number} triple, with or
# without the REST ``/repos/`` prefix. Shared by the adapter's fetch and the label.
_ISSUE_RE = re.compile(r"/(?:repos/)?(?P<owner>[^/]+)/(?P<repo>[^/]+)/issues/(?P<number>\d+)")

# provider → short display code. A provider absent here renders its raw tag.
_PROVIDER_CODES = {"github": "gh"}


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


def pointer_label(pointer: PmPointer) -> str | None:
    """The board-legible ``{provider-code}:{repo}#{number}`` for ``pointer`` (D-075).

    ``None`` when the URL is not issue-shaped — a view degrades to the chunk's stable
    short id rather than rendering a broken label.
    """
    ref = parse_issue_url(pointer.url)
    if ref is None:
        return None
    code = _PROVIDER_CODES.get(pointer.provider, pointer.provider)
    return f"{code}:{ref.repo}#{ref.number}"
