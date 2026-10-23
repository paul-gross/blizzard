"""A landing action's handle on one repository's forge, with that repository's credential already bound.

The Protocol imports no HTTP client, so ``hub/delivery/actions/`` may name it (``bzh:hub-action-purity``);
an action never sees a secret, only a handle that already carries one."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.repositories import ResolvedRepository


@domain_model
@dataclass(frozen=True)
class ForgeReply:
    """One forge call's answer: its HTTP status and its decoded JSON body — ``None`` for an empty body, and
    ``{"message": <text>}`` for a body that is not JSON."""

    status: int
    payload: Any = None


class ForgeUnreachable(Exception):
    """A forge call got no answer at all — a connection failure or its own timeout. Another pass may."""


class IGitHubHandle(Protocol):
    """One repository's GitHub forge: the reads and writes ``land`` makes, each against the ``owner/repo``
    ``repo`` names. Every call raises :class:`ForgeUnreachable` when it gets no answer. Its client is opened
    on the action loop when a pass starts and closed there when the pass ends, so it never outlives its pass
    or crosses event loops."""

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def closed_pulls(self, repo: str, *, base: str, page: int) -> ForgeReply:
        """One page (of 100) of ``repo``'s closed pull requests into ``base``."""
        ...

    async def open_pulls(self, repo: str) -> ForgeReply: ...

    async def open_pull(self, repo: str, *, title: str, head: str, base: str, body: str) -> ForgeReply: ...

    async def pull(self, repo: str, number: int) -> ForgeReply: ...

    async def update_branch(self, repo: str, number: int, *, expected_head_sha: str) -> ForgeReply: ...

    async def merge(self, repo: str, number: int, *, sha: str, commit_message: str, method: str) -> ForgeReply: ...

    async def check_runs(self, repo: str, ref: str) -> ForgeReply: ...

    async def rerequest_check_run(self, repo: str, check_id: object) -> ForgeReply: ...

    async def compare(self, repo: str, base: str, head: str) -> ForgeReply:
        """The forge's ``base...head`` comparison."""
        ...

    async def branch(self, repo: str, branch: str) -> ForgeReply:
        """``branch``'s git ref, its live tip under ``object.sha``."""
        ...


#: Each resolved repository's record id to its forge handle — handed only to an action that lands.
type VisitForges = Mapping[str, IGitHubHandle]

#: Builds the handle for one resolved repository from its revealed credential.
type ForgeHandleFactory = Callable[[ResolvedRepository, str], IGitHubHandle]
