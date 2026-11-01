"""``SessionService`` (``bzh:controller-read-only``) — the application service
``login``/``logout`` take instead of the raw ``IWriteSessionStore`` seam, proven here
against a small fake store rather than through the CLI (that's ``tests/test_hub_cli_login.py``)."""

from __future__ import annotations

import pytest

from blizzard.hub.cli.session_service import SessionService
from tests.session_store_fakes import InMemorySessionStore

pytestmark = pytest.mark.unit


def test_login_saves_the_token_to_the_underlying_store() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)

    service.login("http://hub.local:8421", "the-token")

    assert store.saved == [("http://hub.local:8421", "the-token")]


def test_logout_revokes_then_deletes() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)
    calls: list[str] = []

    service.logout("http://hub.local:8421", revoke=lambda: calls.append("revoked"))

    assert calls == ["revoked"]
    assert store.deleted == ["http://hub.local:8421"]


def test_logout_still_deletes_when_revoke_raises() -> None:
    store = InMemorySessionStore()
    service = SessionService(store)

    def failing_revoke() -> None:
        raise RuntimeError("hub unreachable")

    service.logout("http://hub.local:8421", revoke=failing_revoke)

    assert store.deleted == ["http://hub.local:8421"]


def test_load_delegates_to_the_underlying_store() -> None:
    store = InMemorySessionStore()
    store.save("http://hub.local:8421", "stored-token")
    service = SessionService(store)

    assert service.load("http://hub.local:8421") == "stored-token"
