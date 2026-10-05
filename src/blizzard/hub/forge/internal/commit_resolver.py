"""The GitHub-backed commit resolver — the real forge check
behind `garden_delivery.CommitResolver`: resolves whether a cited commit exists on a
repo, degrading to ``None`` (well-formedness only) when the forge cannot answer. Confined to ``internal/``
(``bzh:dependency-inversion``); ``httpx`` is used only here, as `github_work_source.py`
uses its own client only in its own `internal/`."""

from __future__ import annotations

from datetime import datetime

import httpx

from blizzard.foundation.repo_ref import RepoRef
from blizzard.hub.domain.garden.delivery.validation import CommitResolution


class GitHubCommitResolver:
    """Resolves a ``(repo, commit)`` pair against a GitHub REST v3 forge — never raises
    (`garden_delivery.CommitResolver`'s own contract): a transport failure or an unexpected
    status degrades to ``None`` rather than rejecting or crashing a delivery on a resolver
    fault. The forge, owner and token come with each call, from the repository record the
    caller resolved — nothing about a forge is held here."""

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def resolve(
        self, repo: str, commit: str, *, forge_url: str, owner: str, token: str | None
    ) -> CommitResolution | None:
        """A :class:`CommitResolution` for ``repo`` under ``owner`` on ``forge_url``, or ``None``
        when the read itself failed (a non-404 error status, or a transport error). This method
        must never raise."""
        qualified = RepoRef(host="", owner=owner, name=repo.rpartition("/")[2]).qualified
        headers = {"Authorization": f"token {token}"} if token else None
        try:
            resp = self._client.get(f"{forge_url.rstrip('/')}/repos/{qualified}/commits/{commit}", headers=headers)
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
        """The commit's own authored instant from the same body the
        200 response already carries — no second forge round trip. Malformed or missing
        JSON degrades to `None` (unattributed, never guessed)."""
        try:
            raw = resp.json()["commit"]["author"]["date"]
        except (ValueError, KeyError, TypeError):
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return None
