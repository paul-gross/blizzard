"""Deriving a forge coordinate from a repo's own origin URL — unit tier.

The coordinate delivery addresses a repo by used to be *reconstructed*: a bare worktree
name plus one workspace-wide ``BZ_FORGE_OWNER``. That is wrong the moment a chunk touches
repos under two owners, and it discards what the declaring repo's ``origin`` already
says. These pin both halves of the replacement: what a URL yields when it encodes an
owner, and — just as load-bearing — that a URL encoding none yields ``None`` rather than
a guess, so the configured fallback still governs the bare/file-backed origins the
verification forge fronts.
"""

from __future__ import annotations

import pytest

from blizzard.hub.delivery.repo_ref import parse_repo_ref

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("origin_url", "host", "owner", "name"),
    [
        ("git@github.com:paul-gross/blizzard.git", "github.com", "paul-gross", "blizzard"),
        ("git@github.com:paul-gross/blizzard", "github.com", "paul-gross", "blizzard"),
        ("https://github.com/paul-gross/blizzard.git", "github.com", "paul-gross", "blizzard"),
        ("https://github.com/paul-gross/blizzard/", "github.com", "paul-gross", "blizzard"),
        ("ssh://git@github.com/paul-gross/blizzard.git", "github.com", "paul-gross", "blizzard"),
        # Credentials in the URL are not part of the host.
        ("https://user:tok@git.example.test/team/svc.git", "git.example.test", "team", "svc"),
        # A self-hosted forge on a non-default port.
        ("ssh://git@git.internal:2222/platform/api.git", "git.internal:2222", "platform", "api"),
        # A nested group path resolves to its immediate parent — the segment the REST
        # route wants, not the whole group chain.
        ("https://gitlab.example/group/subgroup/thing.git", "gitlab.example", "subgroup", "thing"),
    ],
)
def test_parses_the_owner_and_name_an_origin_encodes(origin_url: str, host: str, owner: str, name: str) -> None:
    ref = parse_repo_ref(origin_url)

    assert ref is not None
    assert (ref.host, ref.owner, ref.name) == (host, owner, name)
    assert ref.qualified == f"{owner}/{name}"


@pytest.mark.parametrize(
    "origin_url",
    [
        # The verification forge's own shape: flat bare origins that resolve under any
        # owner, which is exactly why the configured-owner fallback still exists.
        "file:///home/pgross/fixture/origins/toy-api.git",
        "file:///origins/toy-api.git",
        "/srv/git/toy-api.git",
        "../sibling-repo",
        # A host with a repo but no owner above it.
        "https://example.test/lonely.git",
        "git@github.com:blizzard.git",
        "",
        "   ",
    ],
)
def test_returns_none_when_the_origin_names_no_owner(origin_url: str) -> None:
    """``None`` is a real answer, not a parse failure. Promoting a parent directory to an
    organization would invent a coordinate that resolves to nothing — strictly worse than
    deferring to the configured default."""
    assert parse_repo_ref(origin_url) is None
