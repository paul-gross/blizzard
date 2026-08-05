"""Deriving a forge coordinate (``owner/name`` — the path a forge's REST routes resolve) from a repo's
own origin URL.

The coordinate is read from the origin URL when the URL encodes one; an origin naming a repo without
naming who owns it yields ``None`` rather than a guess (:func:`parse_repo_ref`). Pure and
dependency-free (``bzh:deterministic-shell``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["RepoRef", "parse_repo_ref"]

# `scp`-style ssh remotes — `git@host:owner/name(.git)` — which urllib does not parse as
# a URL at all (no `//`), and which are the dominant form in practice.
_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")
# Anything with an explicit scheme: `https://host/owner/name`, `ssh://git@host/owner/name`,
# `file:///path/to/name.git`, … The netloc may carry userinfo and a port.
_WITH_SCHEME = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://(?P<netloc>[^/]*)(?P<path>/.*)?$")


@dataclass(frozen=True)
class RepoRef:
    """A repo's forge coordinate: the host it lives on and its ``owner/name`` path."""

    host: str
    owner: str
    name: str

    @property
    def qualified(self) -> str:
        """``owner/name`` — what a forge's REST route takes."""
        return f"{self.owner}/{self.name}"


def parse_repo_ref(origin_url: str) -> RepoRef | None:
    """The ``owner/name`` coordinate ``origin_url`` encodes, or ``None`` if it encodes none.

    ``None`` is a real answer, not a failure (pinned by
    ``tests/test_repo_ref.py::test_returns_none_when_the_origin_names_no_owner``). Only the last two
    path segments matter, so a nested group path yields the immediate parent as the owner."""
    url = origin_url.strip().rstrip("/")
    if not url:
        return None

    scheme_match = _WITH_SCHEME.match(url)
    if scheme_match:
        host = scheme_match.group("netloc").rpartition("@")[2]
        path = (scheme_match.group("path") or "").lstrip("/")
        if not host:
            # `file:///…` has an empty netloc: a filesystem path, owned by nobody.
            return None
    else:
        scp_match = _SCP_LIKE.match(url)
        if scp_match is None:
            return None  # a bare relative/absolute path — no host, no owner
        host = scp_match.group("host").rpartition("@")[2]
        path = scp_match.group("path").lstrip("/")

    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return None  # a name with no owner above it
    return RepoRef(host=host, owner=segments[-2], name=segments[-1])
