"""``GitHubLanding``'s pending-change poll against a recorded ``httpx2`` transport: one conditional, filtered lookup
per poll (component tier)."""

from __future__ import annotations

from typing import cast

from blizzard.hub.delivery.actions.landing.merge_on_checks import PullRequest as _PullRequest
from blizzard.hub.delivery.actions.landing.repo import LandingRun as _Run
from blizzard.hub.delivery.actions.landing.repo import RepoLanding as _Target
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import RepositoryForge
from tests.support.github_landing_harness import REPO, Harness, reply


def test_pending_polls_make_one_conditional_filtered_lookup_each() -> None:
    harness = Harness({("GET", f"{REPO}/pulls"): [reply(200, [], {"ETag": '"v1"'}), reply(304)]})

    async def poll(forge: GitHubLanding) -> None:
        record = ResolvedRepository(
            forge_id=ForgeId("frg_example"), owner="acme", base_branch="main", secret_name="s", record="re_widget"
        )
        target = _Target(
            run=cast(_Run, None),
            repo="acme/widget",
            branch="feature-branch",
            commit="sha1",
            record=record,
            forge=RepositoryForge(RepositoryId("re_widget"), forge, forge, forge, forge.OFFERED),
            forge_label="example",
        )
        for _ in range(11):
            assert await _PullRequest.merged_for_branch(target) is None

    harness.run(poll)
    requests = harness.recorded.requests
    assert len(requests) == 11
    for request in requests[1:]:
        assert request.url.params["state"] == "closed"
        assert request.url.params["head"] == "acme:feature-branch"
        assert request.headers["if-none-match"] == '"v1"'
