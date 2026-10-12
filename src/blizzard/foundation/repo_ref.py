"""Deriving a forge coordinate (``owner/name`` — the path a forge's REST routes resolve) from a repo's
own origin URL.

The coordinate is read from the origin URL when the URL encodes one; an origin naming a repo without
naming who owns it yields ``None`` rather than a guess (:meth:`RepoRef.parse`). Pure and
dependency-free (``bzh:deterministic-shell``)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model

__all__ = ["RepoRef", "pointer_key", "repo_identity"]

# `scp`-style ssh remotes lack the `//` a URL parser needs.
_SCP_LIKE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^:/]+):(?P<path>.+)$")
# The netloc of an explicit-scheme URL may carry userinfo and a port.
_WITH_SCHEME = re.compile(r"^(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*)://(?P<netloc>[^/]*)(?P<path>/.*)?$")


@domain_model
@dataclass(frozen=True)
class RepoRef:
    """A repo's forge coordinate: the host it lives on and its ``owner/name`` path."""

    host: str
    owner: str
    name: str

    @classmethod
    def parse(cls, origin_url: str) -> RepoRef | None:
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
        return cls(host=host, owner=segments[-2], name=segments[-1])

    @property
    def qualified(self) -> str:
        """``owner/name`` — what a forge's REST route takes."""
        return f"{self.owner}/{self.name}"


def repo_identity(origin_url: str | None, name: str) -> str:
    """The one identity a commit pointer's repository is known by.

    The ``owner/name`` coordinate ``origin_url`` encodes when it names an owner; otherwise ``name``
    as given, so a name already qualified (a stored row with no origin) passes through unchanged."""
    ref = RepoRef.parse(origin_url) if origin_url else None
    return ref.qualified if ref else name


def pointer_key(origin_url: str | None, name: str) -> str:
    """The key commit pointers group by: ``repo_identity`` qualified by the origin's host.

    The host is lowercased with its port and userinfo dropped, so one repository's scp-style and
    https origins give one key; the same ``owner/name`` on two forge hosts gives two. An origin
    that names no host falls back to :func:`repo_identity`."""
    ref = RepoRef.parse(origin_url) if origin_url else None
    if ref is None:
        return name
    host = ref.host.lower().rpartition("@")[2]
    host = host.rpartition(":")[0] if re.search(r":\d*$", host) and not host.endswith("]") else host
    return f"{host}/{ref.qualified}" if host else ref.qualified
