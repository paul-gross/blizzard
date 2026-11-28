"""Explicit collaborators retain the request-local authentication and configuration caches."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import Request

from blizzard.hub.api.auth_session import resolve_identity
from blizzard.hub.api.request_config import config_for_request
from blizzard.hub.auth.hashing import SessionId
from blizzard.hub.config import AuthConfig, HubConfig


def _request(headers: list[tuple[bytes, bytes]]) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(config=SimpleNamespace(auth=SimpleNamespace(mode="oauth"))))
    return Request({"type": "http", "headers": headers, "app": app})


def test_session_cookie_wins_and_identity_is_cached() -> None:
    request = _request([(b"cookie", b"bz_session=cookie"), (b"authorization", b"Bearer bearer")])
    sessions = Mock()
    auth = Mock()
    identity = auth.touch_session.return_value

    config = HubConfig(root=Path("."), db_url="sqlite://", auth=AuthConfig(mode="oauth"))
    assert resolve_identity(request, sessions, auth, config) is identity
    assert resolve_identity(request, sessions, auth, config) is identity
    sessions.get_by_hash.assert_called_once_with(SessionId("cookie").hash)
    auth.touch_session.assert_called_once_with(sessions.get_by_hash.return_value, tenant=None)


def test_request_config_is_cached_across_explicit_helper_calls() -> None:
    request = _request([])
    source = Mock()

    assert config_for_request(request, source) is source.resolved.return_value
    assert config_for_request(request, source) is source.resolved.return_value
    source.resolved.assert_called_once_with()
