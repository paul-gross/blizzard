"""A landing action's handle on one repository's forge, with that repository's credential already bound.

The Protocol imports no HTTP client, so ``hub/delivery/actions/`` may name it (``bzh:hub-action-purity``);
an action never sees a secret, only a handle that already carries one."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Protocol

from blizzard.hub.domain.config.repositories import ResolvedRepository


class IGitHubHandle(Protocol):
    """One repository's GitHub forge. Its client is opened on the action loop when a pass starts and
    closed there when the pass ends, so it never outlives its pass or crosses event loops."""

    async def open(self) -> None: ...

    async def close(self) -> None: ...


#: Each resolved repository's record id to its forge handle — handed only to an action that lands.
type VisitForges = Mapping[str, IGitHubHandle]

#: Builds the handle for one resolved repository from its revealed credential.
type ForgeHandleFactory = Callable[[ResolvedRepository, str], IGitHubHandle]
