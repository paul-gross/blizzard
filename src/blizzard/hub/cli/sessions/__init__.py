"""``blizzard hub login``'s local session-token store (issue #96) — CLI-client state, not
hub daemon state: session bearers keyed by hub base URL under the user config dir,
owner-only (``0600``; parent dir ``0700``)."""

from __future__ import annotations

from typing import Protocol


class IReadSessionStore(Protocol):
    """The read-only seam over the local session store (``bzh:controller-read-only``):
    loads a stored token for a hub URL, with no ability to write or delete one."""

    def load(self, hub_url: str) -> str | None: ...


class IWriteSessionStore(IReadSessionStore, Protocol):
    """The full seam over the local session store: read access, plus saving or
    deleting a token, reserved for the verbs that legitimately mutate it."""

    def save(self, hub_url: str, token: str) -> None: ...

    def delete(self, hub_url: str) -> None: ...
