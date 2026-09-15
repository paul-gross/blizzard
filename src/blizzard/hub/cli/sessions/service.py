"""The CLI's session-write application service (hub:98, ``bzh:controller-read-only``) —
owns login's token save and logout's best-effort-revoke-then-always-delete ordering, so no
click controller holds direct write access to the local session store."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from blizzard.hub.cli.sessions import IReadSessionStore, IWriteSessionStore


@dataclass(frozen=True)
class SessionService:
    """Wraps the write seam; also delegates ``load`` so the same instance can serve as the
    hub group's shared ``ctx.obj`` for every other (read-only) verb."""

    _store: IWriteSessionStore

    def load(self, hub_url: str) -> str | None:
        return self._store.load(hub_url)

    def login(self, hub_url: str, token: str) -> None:
        self._store.save(hub_url, token)

    def logout(self, hub_url: str, revoke: Callable[[], object]) -> None:
        """``revoke`` is best-effort (issue #96) — the controller supplies it, and any
        failure from it must never skip the local delete."""
        with contextlib.suppress(Exception):  # best-effort hub revoke — the local delete must still happen
            revoke()
        self._store.delete(hub_url)


def _conforms_session_service_read(x: SessionService) -> IReadSessionStore:
    return x
