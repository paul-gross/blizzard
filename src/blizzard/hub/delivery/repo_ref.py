"""Deriving a forge coordinate (``owner/name``) from a repo's own origin URL.

Delivery addresses a repo as ``owner/name`` — the path a forge's REST routes resolve.
That coordinate used to be *reconstructed*: the declaration carried a bare worktree name
and the land scripts re-attached a single workspace-wide ``BZ_FORGE_OWNER`` to it. That
reconstruction is wrong the moment one chunk touches repos under two different owners,
and it throws away information the declaring repo's ``origin`` already carries.

So the coordinate is now *read* from the origin URL when the URL encodes one, and only
falls back to the configured owner when it does not. The fallback is not vestigial: a git
remote and a forge coordinate are genuinely decoupled in this system. The verification
forge fronts flat bare origins (``file:///…/origins/toy-api.git``) that resolve under any
owner, and the same is true of any mirror, local path, or transport that names a repo
without naming who owns it. Such a URL yields ``None`` here rather than a guess.

Pure and dependency-free (``bzh:deterministic-shell``): the land scripts read the result
out of their injected env, they never parse a URL themselves.
"""

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

    ``None`` is a real answer, not a failure: a bare or file-backed origin names a repo
    without naming an owner, so there is nothing to read and the caller's configured
    default is the only truth available. Guessing an owner from such a URL — treating a
    parent directory as an organization — would invent a coordinate that resolves to
    nothing, which is worse than falling back.

    Only the last two path segments matter, so nested group paths (a self-hosted forge's
    ``group/subgroup/name``) yield the immediate parent as the owner, which is the
    segment its REST route wants.
    """
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
