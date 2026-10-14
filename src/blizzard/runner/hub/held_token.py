"""The hub bearer token a runner process presents, and taking up a corrected one without a restart.

Every runner->hub client presents the process holder's current token. A runner whose token names
another runner than the configured one re-reads its runtime dir's ``.env`` at each identity check, so
restoring the right token there resumes it in-process with nothing lost. A token the process environment or
the host file supplied takes precedence over ``.env``, so it stays fixed until the runner restarts.
The holder never logs a token."""

from __future__ import annotations

import threading
from enum import StrEnum
from typing import Protocol


class IHubTokenFile(Protocol):
    """The runtime dir's ``.env`` assignment of the runner's token."""

    def held(self) -> str:
        """The token the file assigns, or ``""`` when the file or the assignment is absent. Raises
        ``OSError`` when the file cannot be read."""
        ...


class ICurrentHubToken(Protocol):
    """The token every runner->hub call presents."""

    def auth_headers(self) -> dict[str, str]:
        """The ``Authorization`` header carrying the current token; empty when there is none."""
        ...


class TokenReread(StrEnum):
    """What a re-read of the runner's ``.env`` did to the token in use."""

    #: The file names a different token, now the one every hub call presents.
    SWITCHED = "switched"
    #: The file names the token in use, or none: nothing changed.
    UNCHANGED = "unchanged"
    #: The token came from the process environment, which overrides the file: a restart changes it.
    FIXED = "fixed"
    #: The file could not be read: the token in use stays.
    UNREADABLE = "unreadable"


class HubTokenHolder:
    """The process-lifetime :class:`ICurrentHubToken`, seeded with the token the runner started with.
    ``file`` is where a corrected token is re-read from; ``None`` when the process environment supplied
    the token, which the file cannot override."""

    def __init__(self, token: str, *, file: IHubTokenFile | None) -> None:
        self._lock = threading.Lock()
        self._token = token
        self._file = file

    def fixed(self) -> bool:
        """Whether the token came from the process environment, so only a restart changes it."""
        return self._file is None

    def auth_headers(self) -> dict[str, str]:
        with self._lock:
            token = self._token
        return {"Authorization": f"Bearer {token}"} if token else {}

    def reread(self) -> TokenReread:
        """Take up the token the ``.env`` now assigns, when it names one other than the token in use."""
        if self._file is None:
            return TokenReread.FIXED
        try:
            token = self._file.held()
        except OSError:
            return TokenReread.UNREADABLE
        with self._lock:
            if not token or token == self._token:
                return TokenReread.UNCHANGED
            self._token = token
        return TokenReread.SWITCHED


def _conforms_current_hub_token(x: HubTokenHolder) -> ICurrentHubToken:
    return x
