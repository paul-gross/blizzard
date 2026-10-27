"""A GitHub-shaped forge's browser addresses, derived from its REST API base — unit tier."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import RepositoryFields
from blizzard.hub.forge.web import branch_url, web_origin

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("api_base", "origin"),
    [
        ("https://api.github.com", "https://github.com"),
        ("https://api.github.com/", "https://github.com"),
        ("https://ghe.example/api/v3", "https://ghe.example"),
        ("https://ghe.example/api/v3/", "https://ghe.example"),
        ("http://forge.fixture", "http://forge.fixture"),
    ],
)
def test_web_origin_derives_from_the_api_base(api_base: str, origin: str) -> None:
    assert web_origin(api_base) == origin


def _forge(api_base: str, web_base: str | None = None) -> ConfiguredForge:
    return ConfiguredForge(
        forge_id=ForgeId("frg_1"),
        slug="github",
        name="GitHub",
        kind="github",
        fields=ForgeFields(api_base, web_base),
        revision=1,
        created_at=datetime(2026, 10, 25, tzinfo=UTC),
        created_by="op",
    )


_REPOSITORY = RepositoryFields(
    forge_id=ForgeId("frg_1"), owner="acme", repo="widget", base_branch="main", secret_name="token"
)


def test_branch_url_is_the_tree_address_on_the_repository_s_own_coordinate() -> None:
    assert branch_url(_forge("https://api.github.com"), _REPOSITORY, "feat/x") == (
        "https://github.com/acme/widget/tree/feat/x"
    )


def test_a_forges_own_web_base_moves_its_repositories_branch_links() -> None:
    forge = _forge("https://api.github.com", "https://code.acme.example")
    assert branch_url(forge, _REPOSITORY, "feat/x") == "https://code.acme.example/acme/widget/tree/feat/x"
