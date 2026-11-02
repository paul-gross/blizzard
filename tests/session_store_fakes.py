"""An in-memory operator session store — what a CLI test hands the CLI so it never reads or writes the real session
file."""

from __future__ import annotations

from collections.abc import Mapping


class InMemorySessionStore:
    """Holds sessions by hub URL, recording every save and delete it receives."""

    def __init__(self, tokens: Mapping[str, str] | None = None) -> None:
        self.saved: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self._tokens: dict[str, str] = dict(tokens or {})

    def load(self, hub_url: str) -> str | None:
        return self._tokens.get(hub_url)

    def save(self, hub_url: str, token: str) -> None:
        self.saved.append((hub_url, token))
        self._tokens[hub_url] = token

    def delete(self, hub_url: str) -> None:
        self.deleted.append(hub_url)
        self._tokens.pop(hub_url, None)
