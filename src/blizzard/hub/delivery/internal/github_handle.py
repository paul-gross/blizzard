"""The GitHub REST adapter behind :class:`~blizzard.hub.delivery.forge_handle.IGitHubHandle` (package-private).

One handle speaks to one repository's forge with that repository's credential, as the land script's
``LandRun.at`` binds each commit to its own target. Its client is built by :meth:`GitHubHandle.open` on
the loop that runs the pass and closed there, so no client crosses event loops; every call carries its
own timeout."""

from __future__ import annotations

import json
from typing import Any

import httpx2

from blizzard.hub.delivery.forge_handle import ForgeReply, ForgeUnreachable
from blizzard.hub.domain.config.repositories import ResolvedRepository

# The user the land script names on the PRs and merges it makes.
_HUB_USER = "blizzard-hub"

#: Bounds one forge call; a pass makes dozens, all under its own ``action_pass_seconds``.
FORGE_CALL_TIMEOUT_SECONDS = 30.0


class GitHubHandle:
    def __init__(self, forge_api_url: str, token: str, *, timeout: float = FORGE_CALL_TIMEOUT_SECONDS) -> None:
        self._base_url = forge_api_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._client: httpx2.AsyncClient | None = None

    @classmethod
    def of(cls, target: ResolvedRepository, token: str) -> GitHubHandle:
        """The handle on ``target``'s forge, carrying its revealed credential."""
        return cls(target.forge_api_url, token)

    async def open(self) -> None:
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"token {self._token}"
        self._client = httpx2.AsyncClient(base_url=self._base_url, headers=headers)

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def closed_pulls(self, repo: str, *, base: str, page: int) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/pulls?state=closed&base={base}&page={page}&per_page=100")

    async def open_pulls(self, repo: str) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/pulls?state=open")

    async def open_pull(self, repo: str, *, title: str, head: str, base: str, body: str) -> ForgeReply:
        return await self._call(
            "POST",
            f"/repos/{repo}/pulls",
            {"title": title, "head": head, "base": base, "body": body, "user": _HUB_USER},
        )

    async def pull(self, repo: str, number: int) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/pulls/{number}")

    async def update_branch(self, repo: str, number: int, *, expected_head_sha: str) -> ForgeReply:
        return await self._call(
            "PUT", f"/repos/{repo}/pulls/{number}/update-branch", {"expected_head_sha": expected_head_sha}
        )

    async def merge(self, repo: str, number: int, *, sha: str, commit_message: str, method: str) -> ForgeReply:
        return await self._call(
            "PUT",
            f"/repos/{repo}/pulls/{number}/merge",
            {"commit_message": commit_message, "sha": sha, "merge_method": method, "user": _HUB_USER},
        )

    async def check_runs(self, repo: str, ref: str) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/commits/{ref}/check-runs")

    async def rerequest_check_run(self, repo: str, check_id: object) -> ForgeReply:
        return await self._call("POST", f"/repos/{repo}/check-runs/{check_id}/rerequest")

    async def compare(self, repo: str, base: str, head: str) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/compare/{base}...{head}")

    async def branch(self, repo: str, branch: str) -> ForgeReply:
        return await self._call("GET", f"/repos/{repo}/git/ref/heads/{branch}")

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> ForgeReply:
        if self._client is None:
            raise RuntimeError("a forge handle is called only between its open and its close")
        try:
            response = await self._client.request(
                method,
                path,
                content=json.dumps(body).encode() if body is not None else None,
                timeout=self._timeout,
            )
        except httpx2.HTTPError as exc:
            raise ForgeUnreachable(f"{method} {self._base_url}{path}: {exc}") from exc
        return ForgeReply(response.status_code, self._payload(response.content))

    @staticmethod
    def _payload(raw: bytes) -> Any:
        """The body as JSON; one that is not reads as its text under ``message``, as the script read it."""
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return {"message": raw.decode(errors="replace")}
