"""The forge web base (unit tier) — the shared issue-URL parse and branch-link derivation (D-075).

D-107 moved the board-legible pointer *label* (``gh:blizzard#8``) onto the configured
GitHub PM binding (``tests/test_pm_source.py`` now covers its rendering); what remains
here is the issue-URL parse and ``forge_web_base``'s sniff-the-first-pointer origin,
still needed by ``api/chunks.py``'s artifact branch links until the pointer carries a
source explicitly (Phase 3).
"""

from __future__ import annotations

import pytest

from blizzard.hub.pm.label import ForgeWebBase, IssueRef, forge_web_base, parse_issue_url

pytestmark = pytest.mark.unit


def test_api_shaped_repos_url_parses_the_same_triple() -> None:
    assert parse_issue_url("http://forge.local/repos/acme/widget/issues/12") == IssueRef(
        owner="acme", repo="widget", number=12
    )


def test_non_issue_shaped_url_yields_no_ref() -> None:
    assert parse_issue_url("https://github.com/paul-gross/blizzard/pull/9") is None


def test_forge_web_base_derives_origin_and_owner_from_an_issue_pointer() -> None:
    base = forge_web_base(["https://github.com/paul-gross/blizzard/issues/8"])
    assert base == ForgeWebBase(origin="https://github.com", owner="paul-gross")


def test_forge_web_base_skips_non_issue_urls_and_yields_none_when_none_match() -> None:
    assert forge_web_base(["https://github.com/paul-gross/blizzard/pull/9", "not-a-url"]) is None


def test_branch_url_qualifies_a_bare_repo_with_the_pointer_owner() -> None:
    base = ForgeWebBase(origin="https://github.com", owner="paul-gross")
    # A produced artifact names its repo by the worktree dir alone — qualify with the owner.
    assert base.branch_url("blizzard", "feat/x") == "https://github.com/paul-gross/blizzard/tree/feat/x"


def test_branch_url_passes_through_an_already_qualified_repo() -> None:
    base = ForgeWebBase(origin="http://forge.local", owner="acme")
    assert base.branch_url("acme/widget", "feature/widget") == "http://forge.local/acme/widget/tree/feature/widget"
