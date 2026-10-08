"""``GitHubCommitResolver`` — the real forge check behind `garden_delivery.CommitResolver`, and
``RepositoryCommitResolver`` — its binding to the repository records (unit tier). Stubs the ``httpx`` transport
(``tests/test_auth_oauth_factory.py``'s own ``httpx.MockTransport`` shape) — never a real
network call, and never a raise, whatever the transport does."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryFields
from blizzard.hub.domain.config.secrets import SecretName, SecretNotFound, SecretValue
from blizzard.hub.domain.garden.delivery.validation import CommitResolution
from blizzard.hub.forge.internal.commit_resolver import GitHubCommitResolver
from blizzard.hub.forge.repository_commits import RepositoryCommitResolver

pytestmark = pytest.mark.unit

_FORGE_URL = "https://api.github.com"
_TOKEN = "t0k3n"
_OWNER = "acme"
_SHA = "deadbeef"


def _client(handler) -> httpx.Client:  # type: ignore[no-untyped-def]
    return httpx.Client(transport=httpx.MockTransport(handler))


_FORGE = {"forge_url": _FORGE_URL, "owner": _OWNER, "token": _TOKEN}


def test_a_200_resolves_true() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"sha": _SHA})

    resolver = GitHubCommitResolver(_client(handler))

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(exists=True, authored_at=None)
    assert seen[0].url == f"{_FORGE_URL}/repos/{_OWNER}/widget/commits/{_SHA}"
    assert seen[0].headers["authorization"] == f"token {_TOKEN}"


def test_a_200_with_a_commit_body_resolves_the_authored_instant() -> None:
    resolver = GitHubCommitResolver(
        _client(
            lambda request: httpx.Response(
                200, json={"sha": _SHA, "commit": {"author": {"date": "2026-01-02T03:04:05Z"}}}
            )
        )
    )

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(
        exists=True, authored_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    )


def test_a_404_resolves_false() -> None:
    resolver = GitHubCommitResolver(_client(lambda request: httpx.Response(404)))

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(exists=False)


@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
def test_any_other_status_degrades_to_none(status_code: int) -> None:
    resolver = GitHubCommitResolver(_client(lambda request: httpx.Response(status_code)))

    assert resolver.resolve("widget", _SHA, **_FORGE) is None


def test_a_transport_error_degrades_to_none_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    assert GitHubCommitResolver(_client(handler)).resolve("widget", _SHA, **_FORGE) is None


def test_a_malformed_url_component_degrades_to_none_never_raises() -> None:
    """A control character in the repo raises ``httpx.InvalidURL`` at request
    construction, outside the ``httpx.HTTPError`` hierarchy — so this passes only if
    the catch is broad enough to honor the never-raise contract."""

    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not reach the transport when the URL fails to construct")

    assert GitHubCommitResolver(_client(handler)).resolve("a\nb", _SHA, **_FORGE) is None


class _Records:
    def __init__(self, rows: list[ConfiguredRepository]) -> None:
        self.rows = rows

    def get(self, name: str) -> ConfiguredRepository | None:
        return next((row for row in self.rows if row.name == name), None)

    def get_many(self, names: list[str]) -> dict[str, ConfiguredRepository]:
        return {row.name: row for row in self.rows if row.name in names}

    def list_all(self, *, include_retired: bool) -> list[ConfiguredRepository]:
        return [row for row in self.rows if include_retired or not row.retired]


class _Secrets:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def reveal(self, name: SecretName) -> SecretValue:
        if name.value not in self.values:
            raise SecretNotFound(name.value)
        return SecretValue(self.values[name.value])


def _row(name: str, *, owner: str = _OWNER, forge: str = _FORGE_URL, secret: str = "tok", retired: bool = False):  # type: ignore[no-untyped-def]
    return ConfiguredRepository(
        name=name,
        fields=RepositoryFields(forge_api_url=forge, owner=owner, repo=name, base_branch="master", secret_name=secret),
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="op",
        retired=retired,
        retired_at=datetime(2026, 2, 1, tzinfo=UTC) if retired else None,
    )


def _bound(rows, values, handler) -> RepositoryCommitResolver:  # type: ignore[no-untyped-def]
    return RepositoryCommitResolver(
        repositories=_Records(rows), secrets=_Secrets(values), forge=GitHubCommitResolver(_client(handler))
    )


def test_a_cited_repository_resolves_against_its_record_each_call() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    values = {"tok": "first"}
    resolver = _bound([_row("widget")], values, handler)

    resolver.resolve("widget", _SHA)
    values["tok"] = "second"
    resolver.resolve("acme/widget", _SHA)

    assert [r.url for r in seen] == [f"{_FORGE_URL}/repos/acme/widget/commits/{_SHA}"] * 2
    assert [r.headers["authorization"] for r in seen] == ["token first", "token second"]


def test_a_repository_no_record_names_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not contact a forge for a repository no record names")

    resolver = _bound([_row("widget"), _row("old", retired=True)], {"tok": "t"}, handler)

    assert resolver.resolve("gadget", _SHA) is None
    assert resolver.resolve("old", _SHA) is None
    assert resolver.resolve("someone-else/widget", _SHA) is None


def test_an_unrevealable_secret_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("must not contact the forge without a token")

    assert _bound([_row("widget")], {}, handler).resolve("widget", _SHA) is None
