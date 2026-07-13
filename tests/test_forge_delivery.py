"""The GitHub-shaped forge delivery adapter (component tier).

Exercises :class:`~blizzard.hub.delivery.internal.github_forge.GitHubForgeDelivery`'s
real HTTP shaping — PR create + guarded merge — against a tiny GitHub-REST double
(``tests.support.github_double``). The choice of a local double over a
``blizzard-mock`` dev dependency is recorded there.
"""

from __future__ import annotations

import pytest

from blizzard.hub.delivery.forge import LandingDisposition, LandingRequest
from blizzard.hub.delivery.internal.github_forge import GitHubForgeDelivery
from tests.support import github_double

pytestmark = pytest.mark.component


def test_land_opens_a_pr_and_merges_it() -> None:
    forge = GitHubForgeDelivery(github_double())
    result = forge.land(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    assert result.disposition is LandingDisposition.LANDED
    assert result.landed_commit == "merged-abc"


def test_land_maps_an_unmergeable_branch_to_conflict() -> None:
    forge = GitHubForgeDelivery(github_double(conflict_branches={"feat"}))
    result = forge.land(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    assert result.disposition is LandingDisposition.CONFLICT
    assert result.landed_commit is None
    assert "mergeable" in result.detail
