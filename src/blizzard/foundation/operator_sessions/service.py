"""The CLI's session-write application service (``bzh:controller-read-only``) —
owns login's token save and logout's best-effort-revoke-then-always-delete ordering, so no
click controller holds direct write access to the local session store."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from dataclasses import dataclass

from blizzard.foundation.operator_sessions import IReadSessionStore, IWriteSessionStore


@dataclass(frozen=True)
class SessionService:
    """Wraps the write seam, and delegates ``load`` so one instance also serves as a read seam."""

    _store: IWriteSessionStore

    def load(self, hub_url: str) -> str | None:
        return self._store.load(hub_url)

    def login(self, hub_url: str, token: str) -> None:
        self._store.save(hub_url, token)

    def logout(self, hub_url: str, revoke: Callable[[], object]) -> None:
        with contextlib.suppress(Exception):
            revoke()
        self._store.delete(hub_url)


def _conforms_session_service_read(x: SessionService) -> IReadSessionStore:
    return x
