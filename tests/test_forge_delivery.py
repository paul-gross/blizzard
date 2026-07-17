"""The GitHub-shaped forge delivery adapter (component tier).

Exercises :class:`~blizzard.hub.delivery.internal.github_forge.GitHubForgeDelivery`'s
real HTTP shaping — PR create + guarded merge — against a tiny GitHub-REST double
(``tests.support.github_double``). The choice of a local double over a
``blizzard-mock`` dev dependency is recorded there.
"""

from __future__ import annotations

import pytest

from blizzard.hub.delivery.forge import LandingDisposition, LandingRequest, PrDisposition
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


def test_land_qualifies_a_bare_repo_with_the_default_owner() -> None:
    # A produced git_commit artifact names its repo by worktree dir alone (``toy-api``);
    # the forge routes on ``owner/name``, so the binding qualifies it (github_forge._repo_path).
    forge = GitHubForgeDelivery(github_double(), default_owner="blizzard")
    result = forge.land(LandingRequest(repo="toy-api", branch_name="e1", commit_hash="abc"))
    assert result.disposition is LandingDisposition.LANDED
    assert result.landed_commit == "merged-abc"


def test_open_pr_creates_a_pr_and_targets_the_base_branch() -> None:
    double = github_double()
    forge = GitHubForgeDelivery(double)
    handle = forge.open_pr(
        LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc", base_branch="master")
    )
    assert handle.repo == "acme/widget"
    assert handle.number == 1
    assert handle.url.endswith("/pull/1")
    # The PR's base is the request's base_branch (the main->master fix, D-060), not a default.
    assert double.forge_state["pulls"][1]["base"] == "master"  # type: ignore[attr-defined]


def test_open_pr_reuses_an_existing_pr_for_the_same_head() -> None:
    # A redelivery (or a crash between the forge create and the pr.opened fact) re-opens
    # for the same head: GitHub 422s, and the adapter finds and reuses the open PR.
    forge = GitHubForgeDelivery(github_double())
    first = forge.open_pr(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    again = forge.open_pr(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    assert (again.number, again.url) == (first.number, first.url)


def test_check_pr_reports_open_then_merged() -> None:
    double = github_double()
    forge = GitHubForgeDelivery(double)
    handle = forge.open_pr(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    assert forge.check_pr(handle).disposition is PrDisposition.OPEN

    # Merge it out of band (a human on GitHub) — the check now reports the merge.
    double.forge_state["pulls"][handle.number].update(  # type: ignore[attr-defined]
        {"merged": True, "state": "closed", "merge_commit_sha": "landed-abc"}
    )
    state = forge.check_pr(handle)
    assert state.disposition is PrDisposition.MERGED
    assert state.landed_commit == "landed-abc"


def test_check_pr_reports_closed_without_merge() -> None:
    double = github_double()
    forge = GitHubForgeDelivery(double)
    handle = forge.open_pr(LandingRequest(repo="acme/widget", branch_name="feat", commit_hash="abc"))
    double.forge_state["pulls"][handle.number]["state"] = "closed"  # type: ignore[attr-defined]
    assert forge.check_pr(handle).disposition is PrDisposition.CLOSED
