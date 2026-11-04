"""Helpers for the github landing tests, shared with other test modules."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import httpx2

from blizzard.foundation.clock import FixedClock
from blizzard.hub.forge.budget import BudgetReading
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding

_NOW = datetime(2026, 10, 4, 12, 0, 0, tzinfo=UTC)


REPO = "/repos/acme/widget"


type _Answer = httpx2.Response | Exception


def reply(status: int, body: Any = None, headers: dict[str, str] | None = None) -> httpx2.Response:
    content = b"" if body is None else json.dumps(body).encode()
    return httpx2.Response(status, content=content, headers=headers)


class _Recorded:
    """Answers each ``(method, path)`` from its queue of recorded answers — the last one repeats — and records every
    request it was sent, with whether the write lock was held as it arrived."""

    def __init__(self, routes: Mapping[tuple[str, str], Sequence[_Answer]], lock: asyncio.Lock | None = None) -> None:
        self._routes = {route: list(answers) for route, answers in routes.items()}
        self._lock = lock
        self.requests: list[httpx2.Request] = []
        self.locked: list[bool] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        self.locked.append(self._lock is not None and self._lock.locked())
        queue = self._routes[(request.method, request.url.path)]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class Harness:
    def __init__(
        self, routes: Mapping[tuple[str, str], Sequence[_Answer]], *, api_url: str = "https://api.github.com"
    ) -> None:
        self.lock = asyncio.Lock()
        self.recorded = _Recorded(routes, self.lock)
        self.clock = FixedClock(_NOW)
        self.sleeps: list[float] = []
        self.lock_during_sleep: list[bool] = []
        self.learned: list[BudgetReading] = []
        binding = ForgeBinding(
            kind="github",
            api_url=api_url,
            owner="acme",
            repo="widget",
            token="tok",
            write_lock=lambda: self.lock,
            dialect=rate_dialect("github"),
            learn=self.learned.append,
        )
        self.forge = GitHubLanding(
            binding, clock=self.clock, sleep=self._sleep, transport=httpx2.MockTransport(self.recorded)
        )

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.lock_during_sleep.append(self.lock.locked())

    def run[T](self, call: Callable[[GitHubLanding], Awaitable[T]]) -> T:
        async def go() -> T:
            try:
                return await call(self.forge)
            finally:
                self.forge.close()
                await asyncio.sleep(0)

        return asyncio.run(go())


def pull(
    *, number: int = 7, state: str = "open", mergeable_state: Any = "clean", merged_at: str | None = None, **extra: Any
) -> dict[str, Any]:
    return {
        "number": number,
        "state": state,
        "mergeable_state": mergeable_state,
        "merged_at": merged_at,
        "head": {"ref": "feat", "sha": "h1"},
        "base": {"ref": "main", "sha": "b1"},
        "html_url": f"https://github.com/acme/widget/pull/{number}",
        "merge_commit_sha": "m1",
        **extra,
    }


def workflow_run(status: Any = "completed", conclusion: Any = "success", **extra: Any) -> dict[str, Any]:
    return {"id": 11, "name": "build", "status": status, "conclusion": conclusion, **extra}
