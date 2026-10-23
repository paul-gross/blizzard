"""``GitHubCommitResolver`` — the real forge check behind `garden_delivery.CommitResolver`, and
``RepositoryCommitResolver`` — its binding to the repository records (unit tier). Stubs the ``httpx2`` transport
(``tests/test_auth_oauth_factory.py``'s own ``httpx2.MockTransport`` shape) — never a real
network call, and never a raise, whatever the transport does."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import httpx2
import pytest

from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryFields,
    RepositoryId,
    RepositorySlug,
    resolve_repository_reference,
)
from blizzard.hub.domain.config.secrets import SecretName, SecretNotFound, SecretValue
from blizzard.hub.domain.garden.delivery.validation import CommitResolution, CommitResolver
from blizzard.hub.domain.projects.links import RepositoryLink
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.forge.internal.commit_resolver import GitHubCommitResolver
from blizzard.hub.forge.repository_commits import RepositoryCommitResolver

pytestmark = pytest.mark.unit

_FORGE_URL = "https://api.github.com"
_TOKEN = "t0k3n"
_OWNER = "acme"
_SHA = "deadbeef"


def _client(handler) -> httpx2.Client:  # type: ignore[no-untyped-def]
    return httpx2.Client(transport=httpx2.MockTransport(handler))


_FORGE = {"forge_url": _FORGE_URL, "owner": _OWNER, "token": _TOKEN}


def test_a_200_resolves_true() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"sha": _SHA})

    resolver = GitHubCommitResolver(_client(handler))

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(exists=True, authored_at=None)
    assert seen[0].url == f"{_FORGE_URL}/repos/{_OWNER}/widget/commits/{_SHA}"
    assert seen[0].headers["authorization"] == f"token {_TOKEN}"


def test_a_200_with_a_commit_body_resolves_the_authored_instant() -> None:
    resolver = GitHubCommitResolver(
        _client(
            lambda request: httpx2.Response(
                200, json={"sha": _SHA, "commit": {"author": {"date": "2026-01-02T03:04:05Z"}}}
            )
        )
    )

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(
        exists=True, authored_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    )


def test_a_404_resolves_false() -> None:
    resolver = GitHubCommitResolver(_client(lambda request: httpx2.Response(404)))

    assert resolver.resolve("widget", _SHA, **_FORGE) == CommitResolution(exists=False)


@pytest.mark.parametrize("status_code", [401, 403, 500, 503])
def test_any_other_status_degrades_to_none(status_code: int) -> None:
    resolver = GitHubCommitResolver(_client(lambda request: httpx2.Response(status_code)))

    assert resolver.resolve("widget", _SHA, **_FORGE) is None


def test_a_transport_error_degrades_to_none_never_raises() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("boom", request=request)

    assert GitHubCommitResolver(_client(handler)).resolve("widget", _SHA, **_FORGE) is None


def test_a_malformed_url_component_degrades_to_none_never_raises() -> None:
    """A control character in the repo raises ``httpx2.InvalidURL`` at request
    construction, outside the ``httpx2.HTTPError`` hierarchy — so this passes only if
    the catch is broad enough to honor the never-raise contract."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("must not reach the transport when the URL fails to construct")

    assert GitHubCommitResolver(_client(handler)).resolve("a\nb", _SHA, **_FORGE) is None


class _Records:
    def __init__(self, rows: list[ConfiguredRepository]) -> None:
        self.rows = rows

    def get(self, repository_id: RepositoryId) -> ConfiguredRepository | None:
        return next((row for row in self.rows if row.repository_id == repository_id), None)

    def get_many(self, repository_ids: list[RepositoryId]) -> dict[RepositoryId, ConfiguredRepository]:
        return {row.repository_id: row for row in self.rows if row.repository_id in repository_ids}

    def get_by_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        return next((row for row in self.rows if row.slug == slug.value), None)

    def get_by_former_slug(self, slug: RepositorySlug) -> ConfiguredRepository | None:
        return None

    def resolve(self, value: str) -> ConfiguredRepository | None:
        return resolve_repository_reference(value, self)

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
        repository_id=RepositoryId(f"repo_{name}"),
        slug=name,
        name=name,
        fields=RepositoryFields(forge_api_url=forge, owner=owner, repo=name, base_branch="master", secret_name=secret),
        revision=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        created_by="op",
        retired=retired,
        retired_at=datetime(2026, 2, 1, tzinfo=UTC) if retired else None,
    )


class _Links:
    """The standing repository links per project; only ``repositories_of`` is read."""

    def __init__(self, linked: dict[ProjectId, set[str]]) -> None:
        self.linked = linked

    def repositories_of(self, project_id: ProjectId) -> list[RepositoryLink]:
        names = sorted(self.linked.get(project_id, set()))
        return [
            RepositoryLink(project_id=project_id, repository_id=RepositoryId(f"repo_{name}"), revision=1)
            for name in names
        ]


_PROJECT = ProjectId("widgets")


def _bound(rows, values, handler, *, linked: set[str] | None = None) -> CommitResolver:  # type: ignore[no-untyped-def]
    """The resolver of :data:`_PROJECT`, which links ``linked`` — every row when ``None``."""
    return RepositoryCommitResolver(
        repositories=_Records(rows),
        secrets=_Secrets(values),
        forge=GitHubCommitResolver(_client(handler)),
        links=_Links({_PROJECT: linked if linked is not None else {row.slug for row in rows}}),  # type: ignore[arg-type]
    ).for_project(_PROJECT)


def test_a_cited_repository_resolves_against_its_record_each_call() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    values = {"tok": "first"}
    resolver = _bound([_row("widget")], values, handler)

    resolver("widget", _SHA)
    values["tok"] = "second"
    resolver("acme/widget", _SHA)

    assert [r.url for r in seen] == [f"{_FORGE_URL}/repos/acme/widget/commits/{_SHA}"] * 2
    assert [r.headers["authorization"] for r in seen] == ["token first", "token second"]


def test_a_repository_no_record_names_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("must not contact a forge for a repository no record names")

    resolver = _bound([_row("widget"), _row("old", retired=True)], {"tok": "t"}, handler)

    assert resolver("gadget", _SHA) is None
    assert resolver("old", _SHA) is None
    assert resolver("someone-else/widget", _SHA) is None


def test_an_unrevealable_secret_degrades_to_none_without_a_request() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        pytest.fail("must not contact the forge without a token")

    assert _bound([_row("widget")], {}, handler)("widget", _SHA) is None


def test_only_the_projects_linked_repositories_reach_the_forge() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    resolver = _bound([_row("widget"), _row("gadget")], {"tok": "t"}, handler, linked={"widget"})

    assert resolver("gadget", _SHA) is None
    assert resolver("widget", _SHA) == CommitResolution(exists=True, authored_at=None)
    assert [r.url for r in seen] == [f"{_FORGE_URL}/repos/acme/widget/commits/{_SHA}"]


def test_the_projects_links_are_read_on_each_call() -> None:
    linked = {"widget"}
    bound = _bound([_row("widget")], {"tok": "t"}, lambda request: httpx2.Response(200), linked=linked)

    assert bound("widget", _SHA) is not None
    linked.clear()
    assert bound("widget", _SHA) is None


def test_a_name_ambiguous_across_the_tenant_resolves_to_the_record_the_project_links() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200)

    fork = _row("widget-fork", owner="other")
    rows = [_row("widget"), replace(fork, fields=replace(fork.fields, repo="widget"))]

    assert _bound(rows, {"tok": "t"}, handler, linked={"widget", "widget-fork"})("widget", _SHA) is None
    assert _bound(rows, {"tok": "t"}, handler, linked={"widget-fork"})("widget", _SHA) is not None
    assert [r.url for r in seen] == [f"{_FORGE_URL}/repos/other/widget/commits/{_SHA}"]
