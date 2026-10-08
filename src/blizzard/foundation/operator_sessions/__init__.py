"""The operator's local session-token store — CLI-client state, not daemon state, so
both CLIs read it. Keying and file mode: ``src/blizzard/foundation/operator_sessions/internal/session_file.py``."""

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
