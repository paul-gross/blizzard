"""``SessionService`` (hub:98, ``bzh:controller-read-only``) — the application service
``login``/``logout`` take instead of the raw ``IWriteSessionStore`` seam, proven here
against a small fake store rather than through the CLI (that's ``test_hub_cli_login.py``)."""

from __future__ import annotations

import pytest

from blizzard.hub.cli.sessions.service import SessionService

pytestmark = pytest.mark.unit


class _FakeStore:
    """A tiny in-memory ``IWriteSessionStore``, recording every call it receives."""

    def __init__(self) -> None:
        self.saved: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self._tokens: dict[str, str] = {}

    def load(self, hub_url: str) -> str | None:
        return self._tokens.get(hub_url)

    def save(self, hub_url: str, token: str) -> None:
        self.saved.append((hub_url, token))
        self._tokens[hub_url] = token

    def delete(self, hub_url: str) -> None:
        self.deleted.append(hub_url)
        self._tokens.pop(hub_url, None)


def test_login_saves_the_token_to_the_underlying_store() -> None:
    store = _FakeStore()
    service = SessionService(store)

    service.login("http://hub.local:8421", "the-token")

    assert store.saved == [("http://hub.local:8421", "the-token")]


def test_logout_revokes_then_deletes() -> None:
    store = _FakeStore()
    service = SessionService(store)
    calls: list[str] = []

    service.logout("http://hub.local:8421", revoke=lambda: calls.append("revoked"))

    assert calls == ["revoked"]
    assert store.deleted == ["http://hub.local:8421"]


def test_logout_still_deletes_when_revoke_raises() -> None:
    store = _FakeStore()
    service = SessionService(store)

    def failing_revoke() -> None:
        raise RuntimeError("hub unreachable")

    service.logout("http://hub.local:8421", revoke=failing_revoke)

    assert store.deleted == ["http://hub.local:8421"]


def test_load_delegates_to_the_underlying_store() -> None:
    store = _FakeStore()
    store.save("http://hub.local:8421", "stored-token")
    service = SessionService(store)

    assert service.load("http://hub.local:8421") == "stored-token"
