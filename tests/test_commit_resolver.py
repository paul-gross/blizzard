"""``GitHubCommitResolver`` — the real forge check behind `garden_delivery.CommitResolver`
(blizzard#393 Phase 4, D2, unit tier). Stubs the ``httpx`` transport
(``test_auth_oauth_factory.py``'s own ``httpx.MockTransport`` shape) — never a real
network call, and never a raise, whatever the transport does."""

from __future__ import annotations

import httpx
import pytest

from blizzard.hub.forge.internal.commit_resolver import GitHubCommitResolver

pytestmark = pytest.mark.unit

_FORGE_URL = "https://api.github.com"
_TOKEN = "t0k3n"
_OWNER = "acme"
_SHA = "deadbeef"


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_a_200_resolves_true() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"sha": _SHA})

    resolver = GitHubCommitResolver(_client(handler), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=_OWNER)

    assert resolver.resolve("widget", _SHA) is True
    assert seen[0].url == f"{_FORGE_URL}/repos/{_OWNER}/widget/commits/{_SHA}"
    assert seen[0].headers["authorization"] == f"token {_TOKEN}"


def test_a_404_resolves_false() -> None:
    resolver = GitHubCommitResolver(
        _client(lambda request: httpx.Response(404)), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=_OWNER
    )

    assert resolver.resolve("widget", _SHA) is False


@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
def test_any_other_status_degrades_to_none(status_code: int) -> None:
    resolver = GitHubCommitResolver(
        _client(lambda request: httpx.Response(status_code)),
        forge_url=_FORGE_URL,
        forge_token=_TOKEN,
        forge_owner=_OWNER,
    )

    assert resolver.resolve("widget", _SHA) is None


def test_no_forge_configured_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not contact the forge when none is configured")

    resolver = GitHubCommitResolver(_client(handler), forge_url=None, forge_token=None, forge_owner=_OWNER)

    assert resolver.resolve("widget", _SHA) is None


def test_a_bare_repo_with_no_forge_owner_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not contact the forge for an unqualifiable repo")

    resolver = GitHubCommitResolver(_client(handler), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=None)

    assert resolver.resolve("widget", _SHA) is None


def test_an_already_qualified_repo_is_used_as_is() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    resolver = GitHubCommitResolver(_client(handler), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=_OWNER)

    resolver.resolve("someone-else/widget", _SHA)

    assert seen[0].url == f"{_FORGE_URL}/repos/someone-else/widget/commits/{_SHA}"


def test_a_transport_error_degrades_to_none_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    resolver = GitHubCommitResolver(_client(handler), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=_OWNER)

    assert resolver.resolve("widget", _SHA) is None


def test_a_malformed_url_component_degrades_to_none_never_raises() -> None:
    """A control character in the repo raises ``httpx.InvalidURL`` at request
    construction, outside the ``httpx.HTTPError`` hierarchy — so this passes only if
    the catch is broad enough to honor the never-raise contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not reach the transport when the URL fails to construct")

    resolver = GitHubCommitResolver(_client(handler), forge_url=_FORGE_URL, forge_token=_TOKEN, forge_owner=_OWNER)

    assert resolver.resolve("a\nb", _SHA) is None
