"""The hub bearer token a runner process presents, and taking up a replaced one without a restart.

Every runner->hub client presents the process holder's current token. The token lives sealed in the host store, and
at every identity check the holder compares the row's revision with the one it holds, opening the row only when the
revision has moved: a token set again takes effect at the next check, whether or not the runner was refusing. A row
that does not open under the host's key leaves the holder without a token, refusing until the token is set again.
The holder never logs a token."""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.secrets.sealing import SecretUnreadable
from blizzard.runner.hub.sealed_tokens import OpenedHubToken, RunnerHubTokenSource, StoredToken, StoredTokenState


class IHubTokenSource(Protocol):
    """The runner's stored token row."""

    def revision(self) -> int | None:
        """The revision the row is at, or ``None`` when the runner holds no row."""
        ...

    def open(self) -> OpenedHubToken | None:
        """The row's token and its revision, ``None`` when there is no row; raises :class:`SecretUnreadable` when
        the row does not open under the host's key."""
        ...


class ICurrentHubToken(Protocol):
    """The token every runner->hub call presents."""

    def auth_headers(self) -> dict[str, str]:
        """The ``Authorization`` header carrying the current token; empty when there is none."""
        ...


class TokenRefresh(StrEnum):
    """What comparing the held revision with the row's did to the token in use."""

    #: The row moved to a new revision, whose token every hub call now presents.
    SWITCHED = "switched"
    #: The row is at the revision held, or there is none: nothing changed.
    UNCHANGED = "unchanged"
    #: The row does not open under the host's key: the token in use, if any, stays.
    UNREADABLE = "unreadable"


class HubTokenHolder:
    """The process-lifetime :class:`ICurrentHubToken`, seeded with the token the runner started with and the
    revision it was opened at. ``source`` is the runner's row; ``None`` holds the token as it is."""

    def __init__(
        self,
        token: str,
        *,
        revision: int | None = None,
        unreadable_key: str | None = None,
        source: IHubTokenSource | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._token = token
        self._revision = revision
        self._unreadable_key = unreadable_key
        self._source = source

    @classmethod
    def of(cls, stored: StoredToken, source: IHubTokenSource | None) -> HubTokenHolder:
        """The holder a runner starts with from its stored-token outcome: an unreadable row holds no token."""
        unreadable = stored.key_id if stored.state is StoredTokenState.UNREADABLE else None
        return cls(stored.value, revision=stored.revision, unreadable_key=unreadable, source=source)

    def auth_headers(self) -> dict[str, str]:
        with self._lock:
            token = self._token
        return {"Authorization": f"Bearer {token}"} if token else {}

    def unreadable_key(self) -> str | None:
        """The key generation the runner's row names when it does not open, so no token is held; else ``None``."""
        with self._lock:
            return self._unreadable_key

    def refresh(self) -> TokenRefresh:
        """Take up the row's token when its revision has moved from the one held, or while the row did not open."""
        if self._source is None:
            return TokenRefresh.UNCHANGED
        revision = self._source.revision()
        with self._lock:
            if revision is None or (revision == self._revision and self._unreadable_key is None):
                return TokenRefresh.UNCHANGED
        try:
            opened = self._source.open()
        except SecretUnreadable as exc:
            with self._lock:
                if self._unreadable_key is not None or not self._token:
                    self._unreadable_key = exc.key_id
                    self._token = ""
            return TokenRefresh.UNREADABLE
        if opened is None:
            return TokenRefresh.UNCHANGED
        with self._lock:
            self._token = opened.value.expose()
            self._revision = opened.revision
            self._unreadable_key = None
        return TokenRefresh.SWITCHED


_current_hub_token_conforms: type[ICurrentHubToken] = HubTokenHolder
_hub_token_source_conforms: type[IHubTokenSource] = RunnerHubTokenSource
