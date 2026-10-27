"""``GitHubLanding``'s ref operations, named refusals, and kept reads against a recorded ``httpx2`` transport (unit
tier)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.internal.github_reads import ANSWERS_MAX, COMPARISONS_MAX, KeptAnswers, KeptComparisons
from blizzard.hub.forge.landing import ForgeRejected, MergeMethod
from tests.test_github_landing import _REPO, _Harness, _pull, _reply, _run

pytestmark = pytest.mark.unit

_TIP = {"object": {"sha": "a" * 40}}
_REF = f"{_REPO}/git/ref/heads/main"
_REFS = f"{_REPO}/git/refs/heads/main"


def test_github_offers_the_four_new_capabilities() -> None:
    new = {Capability.REFS_ADVANCE, Capability.BRANCHES_MERGE, Capability.REFS_CREATE, Capability.REFS_DELETE}
    assert new <= GitHubLanding.OFFERED == FORGE_KIND_CAPABILITIES["github"]


# advance_ref


def test_advance_ref_patches_without_force_when_the_tip_is_the_expected_one() -> None:
    harness = _Harness({("GET", _REF): [_reply(200, _TIP)], ("PATCH", _REFS): [_reply(200, {})]})

    harness.run(lambda forge: forge.advance_ref("main", "a" * 40, "b" * 40))

    sent = json.loads(harness.recorded.requests[-1].content)
    assert sent == {"sha": "b" * 40, "force": False}
    assert all(harness.recorded.locked[i] for i, r in enumerate(harness.recorded.requests) if r.method == "PATCH")


def test_advance_ref_with_a_moved_tip_is_stale_and_sends_no_patch() -> None:
    harness = _Harness({("GET", _REF): [_reply(200, _TIP)]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.advance_ref("main", "c" * 40, "b" * 40))

    assert raised.value.reason == ForgeRejected.STALE
    assert [r.method for r in harness.recorded.requests] == ["GET"]


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        ("Update is not a fast forward", "stale"),
        ("Update is not a fast forward - detail appended", "stale"),
        ("Protected branch update failed for refs/heads/main.", "protected"),
        ("Cannot update this protected ref.", "protected"),
        ("Repository rule violations found", "protected"),
        ('Required status check "ci" is expected.', "protected"),
    ],
)
def test_advance_ref_refusals_are_named_by_message_prefix(message: str, reason: str) -> None:
    harness = _Harness({("GET", _REF): [_reply(200, _TIP)], ("PATCH", _REFS): [_reply(422, {"message": message})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.advance_ref("main", "a" * 40, "b" * 40))

    assert raised.value.reason == reason


# merge_into


def test_merge_into_answers_the_merge_commit_or_none_or_the_named_refusal() -> None:
    path = f"{_REPO}/merges"
    harness = _Harness(
        {
            ("POST", path): [
                _reply(201, {"sha": "m" * 40}),
                _reply(204),
                _reply(409, {"message": "Merge conflict"}),
                _reply(422, {"message": "Repository rule violations found for refs/heads/x-landing"}),
            ]
        }
    )

    assert harness.run(lambda forge: forge.merge_into("x", "r1", "msg")) == "m" * 40
    assert harness.run(lambda forge: forge.merge_into("x", "r1", "msg")) is None
    for reason in ("conflict", "protected"):
        with pytest.raises(ForgeRejected) as raised:
            harness.run(lambda forge: forge.merge_into("x", "r1", "msg"))
        assert raised.value.reason == reason
    sent: dict[str, Any] = json.loads(harness.recorded.requests[0].content)
    assert (sent["base"], sent["head"]) == ("x", "r1")


# create_ref / delete_ref


_CREATE = f"{_REPO}/git/refs"
_EXISTS = {"message": "Reference already exists"}


def test_create_ref_at_the_same_revision_is_not_an_error() -> None:
    harness = _Harness({("POST", _CREATE): [_reply(422, _EXISTS)], ("GET", _REF): [_reply(200, _TIP)]})

    harness.run(lambda forge: forge.create_ref("main", "a" * 40))


def test_create_ref_at_another_revision_is_stale() -> None:
    harness = _Harness({("POST", _CREATE): [_reply(422, _EXISTS)], ("GET", _REF): [_reply(200, _TIP)]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.create_ref("main", "b" * 40))

    assert raised.value.reason == ForgeRejected.STALE


def test_create_ref_under_a_repository_rule_is_protected() -> None:
    harness = _Harness({("POST", _CREATE): [_reply(422, {"message": "Repository rule violations found"})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.create_ref("main", "a" * 40))

    assert raised.value.reason == ForgeRejected.PROTECTED


@pytest.mark.parametrize("status", [404, 422])
def test_delete_ref_of_a_branch_already_gone_is_not_an_error(status: int) -> None:
    harness = _Harness({("DELETE", _REFS): [_reply(status, {"message": "Reference does not exist"})]})

    harness.run(lambda forge: forge.delete_ref("main"))


# merge's method refusal


def test_a_merge_method_refusal_is_method_and_keeps_the_forges_words() -> None:
    message = "Merge commits are not allowed on this repository"
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/merge"): [_reply(405, {"message": message})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.merge(7, "h1", MergeMethod.MERGE, "msg"))

    assert raised.value.reason == ForgeRejected.METHOD
    assert message in str(raised.value)


def test_any_other_405_still_raises_the_forges_message() -> None:
    harness = _Harness({("PUT", f"{_REPO}/pulls/7/merge"): [_reply(405, {"message": "Base branch was modified"})]})

    with pytest.raises(ForgeRejected) as raised:
        harness.run(lambda forge: forge.merge(7, "h1", MergeMethod.MERGE, "msg"))

    assert raised.value.reason == "Base branch was modified"


# recently_closed / change_checks


def test_recently_closed_is_one_page_ordered_by_update() -> None:
    harness = _Harness({("GET", f"{_REPO}/pulls"): [_reply(200, [_pull(number=3, state="closed")])]})

    found = harness.run(lambda forge: forge.recently_closed("main", 20))

    assert [c.number for c in found] == [3]
    params = harness.recorded.requests[0].url.params
    assert (params["state"], params["base"], params["sort"], params["direction"], params["per_page"]) == (
        "closed",
        "main",
        "updated",
        "desc",
        "20",
    )
    assert len(harness.recorded.requests) == 1


def test_change_checks_reads_the_checks_of_the_changes_head() -> None:
    harness = _Harness(
        {
            ("GET", f"{_REPO}/pulls/7"): [_reply(200, _pull())],
            ("GET", f"{_REPO}/commits/h1/check-runs"): [_reply(200, {"check_runs": [_run()]})],
        }
    )

    found = harness.run(lambda forge: forge.change_checks(7))

    assert [c.name for c in found] == ["build"]
    assert harness.recorded.paths() == [f"{_REPO}/pulls/7", f"{_REPO}/commits/h1/check-runs"]


# kept reads


def test_a_304_is_answered_from_the_kept_body_and_sent_conditionally() -> None:
    path = f"{_REPO}/pulls/7"
    harness = _Harness({("GET", path): [_reply(200, _pull(), {"ETag": '"v1"'}), _reply(304)]})

    first = harness.run(lambda forge: forge.change(7))
    both = harness.run(lambda forge: _twice(forge))

    assert both[0] == both[1] == first
    assert harness.recorded.requests[-1].headers["if-none-match"] == '"v1"'


async def _twice(forge: GitHubLanding) -> tuple[Any, Any]:
    return await forge.change(7), await forge.change(7)


def test_checks_always_reach_the_transport_even_after_an_all_completed_answer() -> None:
    path = f"{_REPO}/commits/h1/check-runs"
    harness = _Harness({("GET", path): [_reply(200, {"check_runs": [_run()]}, {"ETag": '"c1"'})]})

    async def thrice(forge: GitHubLanding) -> None:
        for _ in range(3):
            await forge.checks("h1")

    harness.run(thrice)

    assert len(harness.recorded.requests) == 3


def test_a_304_with_nothing_kept_is_asked_again_unconditionally() -> None:
    path = f"{_REPO}/pulls/7"
    harness = _Harness({("GET", path): [_reply(200, _pull())]})
    assert harness.run(lambda forge: forge.change(7)).number == 7
    assert "if-none-match" not in harness.recorded.requests[0].headers


_COMPARE = {
    "status": "ahead",
    "ahead_by": 1,
    "behind_by": 0,
    "total_commits": 1,
    "commits": [{"sha": "c1", "parents": []}],
    "files": [],
}


def test_a_compare_of_two_fixed_revisions_is_read_once() -> None:
    base, head = "a" * 40, "b" * 40
    harness = _Harness({("GET", f"{_REPO}/compare/{base}...{head}"): [_reply(200, _COMPARE)]})

    async def twice(forge: GitHubLanding) -> None:
        await forge.compare(base, head)
        await forge.compare(base, head)

    harness.run(twice)

    assert len(harness.recorded.requests) == 1


def test_a_compare_naming_a_branch_is_sent_every_time_and_conditionally() -> None:
    head = "b" * 40
    harness = _Harness(
        {("GET", f"{_REPO}/compare/main...{head}"): [_reply(200, _COMPARE, {"ETag": '"k"'}), _reply(304)]}
    )

    async def twice(forge: GitHubLanding) -> None:
        await forge.compare("main", head)
        await forge.compare("main", head)

    harness.run(twice)

    assert len(harness.recorded.requests) == 2
    assert harness.recorded.requests[1].headers["if-none-match"] == '"k"'


def test_the_stores_stay_within_their_bounds_and_evict_the_least_recently_used() -> None:
    answers = KeptAnswers(3)
    for key in "abc":
        answers.put(key, _kept(key))
    assert answers.get("a") is not None
    answers.put("d", _kept("d"))
    assert [answers.get(k) is not None for k in "abcd"] == [True, False, True, True]
    assert (ANSWERS_MAX, COMPARISONS_MAX) == (512, 64)
    comparisons = KeptComparisons()
    for n in range(COMPARISONS_MAX + 5):
        comparisons.put(str(n), None)  # type: ignore[arg-type]
    assert len(comparisons) == COMPARISONS_MAX


def _kept(etag: str) -> Any:
    from blizzard.hub.forge.internal.github_reads import KeptAnswer

    return KeptAnswer(etag, 200, b"{}")
