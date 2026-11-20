"""``GitHubLanding``'s pending-change poll against a recorded ``httpx2`` transport: one conditional, filtered lookup
per poll (component tier)."""

from __future__ import annotations

import itertools
import json
from datetime import timedelta
from typing import cast

import pytest

from blizzard.hub.delivery.actions.landing.merge_on_checks import PullRequest as _PullRequest
from blizzard.hub.delivery.actions.landing.repo import LandingRun as _Run
from blizzard.hub.delivery.actions.landing.repo import RepoLanding as _Target
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import RepositoryId, ResolvedRepository
from blizzard.hub.forge.budget import BudgetKey, ForgeBudgetLow, ForgeBudgets, ForgePriority
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import (
    ForgeRateLimited,
    ForgeRejected,
    ForgeUnavailable,
    MergeMethod,
    RepositoryForge,
    UpdateMethod,
    UpdateOutcome,
)
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import BudgetGate
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


@pytest.mark.parametrize("enabled", list(itertools.product((False, True), repeat=3)))
def test_repository_settings_supply_ordered_options(enabled: tuple[bool, bool, bool]) -> None:
    keys = ("allow_merge_commit", "allow_rebase_merge", "allow_squash_merge")
    harness = Harness({("GET", REPO): [reply(200, dict(zip(keys, enabled, strict=True)))]})

    async def read(forge: GitHubLanding) -> None:
        options = await forge.landing_options()
        assert options.methods == tuple(
            method
            for method, allowed in zip(
                (MergeMethod.MERGE, MergeMethod.REBASE, MergeMethod.SQUASH), enabled, strict=True
            )
            if allowed
        )
        assert options.update_method == (UpdateMethod.MERGE if enabled[0] else UpdateMethod.REBASE)

    harness.run(read)


def test_missing_repository_settings_are_unavailable() -> None:
    harness = Harness({("GET", REPO): [reply(200, {})]})
    with pytest.raises(ForgeUnavailable):
        harness.run(lambda forge: forge.landing_options())


def test_rebase_update_uses_graphql_expected_head_and_write_lock() -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [
                reply(200, {"data": {"updatePullRequestBranch": {"pullRequest": {"id": "PR_node"}}}})
            ],
        }
    )

    async def update(forge: GitHubLanding) -> None:
        result = await forge.update_from_base(7, "head", UpdateMethod.REBASE)
        assert result.outcome == UpdateOutcome.ACCEPTED
        assert result.method == UpdateMethod.REBASE

    harness.run(update)
    body = json.loads(harness.recorded.requests[-1].content)
    assert body["variables"]["input"] == {
        "pullRequestId": "PR_node",
        "expectedHeadOid": "head",
        "updateMethod": "REBASE",
    }
    assert harness.recorded.locked == [False, True]


def test_rebase_update_classifies_modified_head_as_stale() -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [
                reply(
                    200,
                    {
                        "data": {"updatePullRequestBranch": None},
                        "errors": [{"message": "Head branch was modified. Review and try the merge again."}],
                    },
                )
            ],
        }
    )
    with pytest.raises(ForgeRejected) as caught:
        harness.run(lambda forge: forge.update_from_base(7, "head", UpdateMethod.REBASE))
    assert caught.value.reason == ForgeRejected.STALE


def test_rebase_update_classifies_graphql_internal_error_as_unavailable() -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [
                reply(200, {"data": None, "errors": [{"type": "INTERNAL_SERVER_ERROR", "message": "retry"}]})
            ],
        }
    )
    with pytest.raises(ForgeUnavailable):
        harness.run(lambda forge: forge.update_from_base(7, "head", UpdateMethod.REBASE))


@pytest.mark.parametrize(
    ("headers", "message", "seconds"),
    [
        ({}, "exhausted", 60),
        ({"Retry-After": "120"}, "exhausted", 120),
        ({"X-RateLimit-Reset": "1791115350"}, "exhausted", 150),
        ({"Retry-After": "5"}, "secondary rate limit", 60),
    ],
)
def test_rebase_update_graphql_rate_limit_is_timed(headers: dict[str, str], message: str, seconds: int) -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [
                reply(200, {"data": None, "errors": [{"type": "RATE_LIMITED", "message": message}]}, headers)
            ],
        }
    )
    with pytest.raises(ForgeRateLimited) as caught:
        harness.run(lambda forge: forge.update_from_base(7, "head", UpdateMethod.REBASE))
    assert caught.value.resume_at == harness.clock.now() + timedelta(seconds=seconds)
    assert len(harness.recorded.requests) == 2
    assert harness.sleeps == []


def test_rebase_graphql_rate_limit_spends_the_forge_budget() -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [reply(200, {"errors": [{"type": "RATE_LIMITED"}]}, {"Retry-After": "120"})],
        }
    )
    budgets = ForgeBudgets(clock=harness.clock, dialects=rate_dialect)
    gate = BudgetGate(budgets, BudgetKey("forge", "token"), ForgePriority.LANDING)

    async def update(forge: GitHubLanding) -> None:
        with pytest.raises(ForgeRateLimited) as refused:
            await gate.spending(forge.update_from_base(7, "head", UpdateMethod.REBASE))
        with pytest.raises(ForgeBudgetLow) as blocked:
            gate.admit()
        assert blocked.value.resume_at == refused.value.resume_at == harness.clock.now() + timedelta(seconds=120)

    harness.run(update)


def test_immutable_git_evidence_and_truncation() -> None:
    commit, tree, blob = "a" * 40, "b" * 40, "c" * 40
    harness = Harness(
        {
            ("GET", f"{REPO}/git/commits/{commit}"): [
                reply(
                    200,
                    {
                        "sha": commit,
                        "tree": {"sha": tree},
                        "parents": [],
                        "author": {"name": "A", "email": "a@example.com", "date": "2026-01-01T00:00:00Z"},
                        "message": "work",
                    },
                )
            ],
            ("GET", f"{REPO}/git/trees/{tree}"): [
                reply(
                    200,
                    {
                        "sha": tree,
                        "truncated": False,
                        "tree": [{"path": "f", "mode": "100644", "type": "blob", "sha": blob}],
                    },
                ),
                reply(200, {"sha": tree, "truncated": True, "tree": []}),
            ],
            ("GET", f"{REPO}/git/blobs/{blob}"): [
                reply(200, {"sha": blob, "encoding": "base64", "content": "aGkK", "size": 3})
            ],
        }
    )

    async def read(forge: GitHubLanding) -> None:
        assert (await forge.commit_evidence(commit)).tree == tree
        assert (await forge.tree_evidence(tree))[0].revision == blob
        assert await forge.blob_evidence(blob) == b"hi\n"
        with pytest.raises(ForgeUnavailable):
            await forge.tree_evidence(tree)
        with pytest.raises(ForgeUnavailable):
            await forge.commit_evidence("main")

    harness.run(read)


@pytest.mark.parametrize(
    "entries",
    [
        [{"path": "empty", "mode": "040000", "type": "tree", "sha": "c" * 40}],
        [{"path": "f", "mode": "100644", "type": "commit", "sha": "c" * 40}],
        [{"path": "../f", "mode": "100644", "type": "blob", "sha": "c" * 40}],
    ],
)
def test_tree_evidence_rejects_unrepresentable_or_malformed_entries(entries: list[dict[str, str]]) -> None:
    tree = "b" * 40
    harness = Harness(
        {("GET", f"{REPO}/git/trees/{tree}"): [reply(200, {"sha": tree, "truncated": False, "tree": entries})]}
    )
    with pytest.raises(ForgeUnavailable):
        harness.run(lambda forge: forge.tree_evidence(tree))


def test_rebase_update_rejects_answer_for_another_pull_request() -> None:
    harness = Harness(
        {
            ("GET", f"{REPO}/pulls/7"): [reply(200, {"node_id": "PR_node"})],
            ("POST", "/graphql"): [reply(200, {"data": {"updatePullRequestBranch": {"pullRequest": {"id": "other"}}}})],
        }
    )
    with pytest.raises(ForgeUnavailable):
        harness.run(lambda forge: forge.update_from_base(7, "head", UpdateMethod.REBASE))
