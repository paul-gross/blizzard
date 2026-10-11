"""A GitHub-shaped forge's browser addresses, derived from its REST API base — unit tier."""

from __future__ import annotations

import pytest

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


def test_branch_url_is_the_tree_address_on_the_repository_s_own_coordinate() -> None:
    repository = RepositoryFields(
        forge_api_url="https://api.github.com",
        owner="acme",
        repo="widget",
        base_branch="main",
        secret_name="token",
    )
    assert branch_url(repository, "feat/x") == "https://github.com/acme/widget/tree/feat/x"
