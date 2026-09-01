"""The GitHub-backed commit resolver (blizzard#393 Phase 4, D2) — the real forge check
behind `garden_delivery.CommitResolver`: resolves whether a cited commit exists on a
repo when the hub has a forge configured and the repo is addressable, degrading to
``None`` (well-formedness only) otherwise. Confined to ``internal/``
(``bzh:dependency-inversion``); ``httpx`` is used only here, as `github_work_source.py`
uses its own client only in its own `internal/`."""

from __future__ import annotations

from datetime import datetime

import httpx

from blizzard.hub.delivery.repo_ref import RepoRef
from blizzard.hub.domain.garden_delivery import CommitResolution


class GitHubCommitResolver:
    """Resolves a ``(repo, commit)`` pair against a GitHub REST v3 forge — never raises
    (`garden_delivery.CommitResolver`'s own contract): a missing/unaddressable forge, or
    any transport failure, degrades to ``None`` rather than rejecting or crashing a
    delivery on a resolver fault. Its bound :meth:`resolve` is itself a `CommitResolver`."""

    def __init__(
        self,
        client: httpx.Client,
        *,
        forge_url: str | None,
        forge_token: str | None,
        forge_owner: str | None,
    ) -> None:
        self._client = client
        self._forge_url = forge_url.rstrip("/") if forge_url else None
        self._forge_token = forge_token
        self._forge_owner = forge_owner

    def resolve(self, repo: str, commit: str) -> CommitResolution | None:
        """A :class:`CommitResolution` when a forge is configured and ``repo`` is
        addressable against it, else ``None`` — no forge configured, ``repo`` cannot be
        qualified with :attr:`_forge_owner`, or the read itself failed (a non-404 error
        status, or a transport error). This method must never raise."""
        if not self._forge_url:
            return None
        qualified = self._qualify(repo)
        if qualified is None:
            return None
        headers = {"Authorization": f"token {self._forge_token}"} if self._forge_token else None
        try:
            resp = self._client.get(f"{self._forge_url}/repos/{qualified}/commits/{commit}", headers=headers)
        except Exception:
            # Broader than ``httpx.HTTPError`` on purpose: a malformed URL component raises
            # at request-construction time, outside that hierarchy. This must never raise.
            return None
        if resp.status_code == 200:
            return CommitResolution(exists=True, authored_at=self._authored_at(resp))
        if resp.status_code == 404:
            return CommitResolution(exists=False)
        return None

    @staticmethod
    def _authored_at(resp: httpx.Response) -> datetime | None:
        """The commit's own authored instant (blizzard#394 D5) from the same body the
        200 response already carries — no second forge round trip. Malformed or missing
        JSON degrades to `None` (D5: unattributed, never guessed)."""
        try:
            raw = resp.json()["commit"]["author"]["date"]
        except (ValueError, KeyError, TypeError):
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None

    def _qualify(self, repo: str) -> str | None:
        """``repo`` as the ``owner/name`` a forge route resolves, or ``None`` when it
        cannot be qualified — ``repo`` is already ``owner/name``, else it needs
        :attr:`_forge_owner` to qualify (mirrors ``land_common.LandRun.repo``)."""
        if "/" in repo:
            return repo
        if not self._forge_owner:
            return None
        return RepoRef(host="", owner=self._forge_owner, name=repo).qualified
