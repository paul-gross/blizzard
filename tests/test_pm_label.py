"""The board-legible pointer label (unit tier) — the shared parse + provider-code map (D-075).

Pins the formatter the chunk views render pointers through: the GitHub issue-URL
parse (shared with the PM adapter — one regex, not one per consumer), the
provider→short-code map, and the two degradations — a non-issue-shaped URL yields no
label, and a provider missing from the map keeps its raw tag rather than an invented
code.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.work import PmPointer
from blizzard.hub.pm.label import IssueRef, parse_issue_url, pointer_label

pytestmark = pytest.mark.unit


def test_github_issue_url_formats_with_the_short_provider_code() -> None:
    pointer = PmPointer(provider="github", url="https://github.com/paul-gross/blizzard/issues/8")
    assert pointer_label(pointer) == "gh:blizzard#8"


def test_api_shaped_repos_url_parses_the_same_triple() -> None:
    assert parse_issue_url("http://forge.local/repos/acme/widget/issues/12") == IssueRef(
        owner="acme", repo="widget", number=12
    )


def test_non_issue_shaped_url_yields_no_label() -> None:
    pointer = PmPointer(provider="github", url="https://github.com/paul-gross/blizzard/pull/9")
    assert parse_issue_url(pointer.url) is None
    assert pointer_label(pointer) is None


def test_provider_without_a_short_code_keeps_its_raw_tag() -> None:
    pointer = PmPointer(provider="forgejo", url="https://forge.example/acme/widgets/issues/7")
    assert pointer_label(pointer) == "forgejo:widgets#7"
