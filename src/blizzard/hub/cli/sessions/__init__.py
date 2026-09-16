"""``blizzard hub login``'s local session-token store (issue #96) — CLI-client state, not
hub daemon state: session bearers keyed by hub base URL under the user config dir,
owner-only (``0600``; parent dir ``0700``)."""

from __future__ import annotations

from typing import Protocol


class IReadSessionStore(Protocol):
    """The read-only seam ``CliContext`` takes (``bzh:controller-read-only``) — every
    read-only hub verb loses the ability to rewrite or delete the operator's session."""

    def load(self, hub_url: str) -> str | None: ...


class IWriteSessionStore(IReadSessionStore, Protocol):
    """The full seam only ``login``/``logout`` take — the two verbs that legitimately
    write the local session store."""

    def save(self, hub_url: str, token: str) -> None: ...

    def delete(self, hub_url: str) -> None: ...
