"""What ``land`` decides from seam answers alone: where a change routes, its check verdicts and findings, the
head gate's first-parent walk and offenders, the base-tip read, the already-merged change lookup, and the
base-merge proof (unit tier)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

import pytest

from blizzard.hub.delivery.actions import base_merge
from blizzard.hub.delivery.actions.land import (
    LandedRevisionUnknown,
    PullRequestLookupError,
    _Escalation,
    _PullRequest,
    _Run,
    _Target,
    gate_head,
)
from blizzard.hub.delivery.actions.land_checks import (
    BOUNCE,
    FAILED,
    PUSH,
    UPDATE,
    WAIT,
    Findings,
    Route,
    Verdict,
    first_parent_chain,
)
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId
from blizzard.hub.domain.config.repositories import ResolvedRepository
from blizzard.hub.forge.landing import (
    Change,
    ChangeState,
    Check,
    CheckOutcome,
    CommitSummary,
    Comparison,
    ComparisonStatus,
    FileChange,
    ForgeNotFound,
    ForgeObject,
    ForgeRejected,
    ForgeUnavailable,
    Mergeability,
)
from tests.github_double import DoubleRepo, GitHubDouble, change, check, comparison

pytestmark = pytest.mark.unit

_REPO = "acme/widget"
_BRANCH = "feature-branch"

_Compares = dict[tuple[str, str], Comparison]


_FORGE = ConfiguredForge(
    forge_id=ForgeId("frg_example"),
    slug="example",
    name="example",
    kind="github",
    fields=ForgeFields("http://forge", None),
    revision=1,
    created_at=datetime(2026, 10, 1, tzinfo=UTC),
    created_by="op",
)


def _target(state: DoubleRepo | None = None, *, commit: str = "sha1") -> _Target:
    record = ResolvedRepository(
        forge_id=_FORGE.forge_id, owner="acme", base_branch="main", secret_name="s", record="re_widget"
    )
    double = GitHubDouble({_REPO: state or DoubleRepo()})
    return _Target(
        run=cast(_Run, None),
        repo=_REPO,
        branch=_BRANCH,
        commit=commit,
        record=record,
        forge=double.forge("re_widget", _REPO),
        forge_label=_FORGE.label,
    )


def _commit(sha: str, *parents: str) -> CommitSummary:
    return CommitSummary(revision=sha, parents=parents)


def _file(
    name: str, blob: str | None, patch: str | None = "@@ -1 +1 @@\n-a\n+b", *, previous: str | None = None
) -> FileChange:
    body = (patch or "").split("\n")
    return FileChange(
        path=name,
        previous_path=previous,
        status="modified",
        blob=blob,
        patch=patch,
        additions=sum(line.startswith("+") for line in body),
        deletions=sum(line.startswith("-") for line in body),
    )


def _compare(
    base: str,
    head: str,
    *,
    status: ComparisonStatus = ComparisonStatus.AHEAD,
    commits: tuple[CommitSummary, ...] = (),
    files: tuple[FileChange, ...] = (),
    merge_base: str | None = "mb0",
    commits_truncated: bool = False,
) -> _Compares:
    found = comparison(status, commits=commits, files=files, merge_base=merge_base, commits_truncated=commits_truncated)
    return {(base, head): found}


def _base_merge(
    *,
    merged_files: tuple[FileChange, ...],
    base_files: tuple[FileChange, ...],
    feature_files: tuple[FileChange, ...] = (),
) -> _Compares:
    """A live head ``merge1``: the submitted ``sha1`` with base commit ``b1`` merged in;
    ``feature_files`` is what the feature side changed since the merge base ``mb0``."""
    return {
        **_compare("mb0", "sha1", files=feature_files),
        **_compare(
            "sha1", "merge1", commits=(_commit("b1", "base0"), _commit("merge1", "sha1", "b1")), files=merged_files
        ),
        **_compare("main", "b1", status=ComparisonStatus.BEHIND),
        **_compare("sha1", "b1", files=base_files),
    }


# --- routing and verdicts -----------------------------------------------------------------------

_PASSED = check()
_FAILED = check(outcome=CheckOutcome.FAILED, detail="failure")
_PENDING = check(outcome=CheckOutcome.PENDING, detail="in_progress")
_NEUTRAL = check(outcome=CheckOutcome.NEUTRAL, detail="skipped")


def test_verdict_fails_when_any_check_among_several_failed() -> None:
    assert Verdict((_PASSED, _PENDING, _FAILED)).decision == FAILED


@pytest.mark.parametrize("checks", [(_PENDING,), (), None], ids=["pending", "empty", "degraded"])
def test_verdict_waits_unless_a_check_failed(checks: tuple[Check, ...] | None) -> None:
    assert Verdict(checks).decision == WAIT


@pytest.mark.parametrize(
    ("checks", "green"),
    [((_PASSED, _NEUTRAL), True), ((_PASSED, _PENDING), False), ((), False), (None, False)],
    ids=["passed-and-neutral", "one-pending", "empty", "degraded"],
)
def test_verdict_is_green_only_on_at_least_one_check_all_passed_or_neutral(
    checks: tuple[Check, ...] | None, green: bool
) -> None:
    assert Verdict(checks).green is green


def test_verdict_red_reads_one_named_check_and_is_unknown_on_a_degraded_read() -> None:
    verdict = Verdict((_FAILED, check("lint")))

    assert verdict.red("build") is True
    assert verdict.red("lint") is False
    assert verdict.red("absent") is False
    assert Verdict(None).red("build") is None


@pytest.mark.parametrize(
    ("route", "decision"),
    [
        (Route(Mergeability.PENDING, merged=True), PUSH),
        (Route(Mergeability.CONFLICTING), BOUNCE),
        (Route(Mergeability.BEHIND), UPDATE),
        (Route(Mergeability.MERGEABLE, verdict=Verdict((_PASSED,))), PUSH),
        (Route(Mergeability.BLOCKED, verdict=Verdict((_PASSED,))), PUSH),
        (Route(Mergeability.MERGEABLE, verdict=Verdict((_PENDING,))), WAIT),
        (Route(Mergeability.MERGEABLE), WAIT),
        (Route(Mergeability.DRAFT, verdict=Verdict((_PASSED,))), WAIT),
        (Route(Mergeability.PENDING, verdict=Verdict((_PASSED,))), WAIT),
    ],
    ids=[
        "merged",
        "conflicting",
        "behind",
        "mergeable-green",
        "blocked-green",
        "mergeable-pending",
        "mergeable-unread",
        "draft",
        "pending",
    ],
)
def test_a_change_routes_by_its_mergeability_and_verdict(route: Route, decision: str) -> None:
    assert route.decision == decision


def test_findings_names_repo_pr_and_each_failing_check() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 42,
            "url": "https://forge/acme/widget/pull/42",
            "decision": FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": False},
            ],
        }
    ]

    text = Findings(tuple(records)).render()

    assert _REPO in text
    assert "42" in text
    assert "https://forge/acme/widget/pull/42" in text
    assert "build" in text
    assert "failure" in text
    assert "https://forge/build/1" in text


def test_findings_names_a_broken_base_as_not_this_change() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 42,
            "url": "https://forge/acme/widget/pull/42",
            "decision": FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": True},
            ],
        }
    ]

    text = Findings(tuple(records)).render()

    assert "not this change" in text


def test_findings_omits_a_base_red_verdict_when_unknown() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 42,
            "url": "https://forge/acme/widget/pull/42",
            "decision": FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": None},
            ],
        }
    ]

    text = Findings(tuple(records)).render()

    assert "base branch" not in text


def test_findings_names_the_still_in_flight_checks_for_a_waiting_repo() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 7,
            "url": "https://forge/acme/widget/pull/7",
            "decision": WAIT,
            "checks": [{"name": "lint", "status": "in_progress"}, {"name": "test", "status": "queued"}],
        }
    ]

    text = Findings(tuple(records)).render()

    assert _REPO in text
    assert "7" in text
    assert "lint" in text
    assert "in_progress" in text
    assert "test" in text
    assert "queued" in text
    # a wait record carries no conclusion/details_url at all — only name + status.
    assert "conclusion" not in text.lower()


def test_findings_joins_multiple_repos() -> None:
    records = [
        {
            "repo": "acme/widget",
            "number": 1,
            "url": "https://forge/acme/widget/pull/1",
            "decision": FAILED,
            "checks": [{"name": "build", "conclusion": "failure", "details_url": "https://forge/1", "base_red": False}],
        },
        {
            "repo": "acme/gadget",
            "number": 2,
            "url": "https://forge/acme/gadget/pull/2",
            "decision": WAIT,
            "checks": [{"name": "test", "status": "queued"}],
        },
    ]

    text = Findings(tuple(records)).render()

    assert "acme/widget" in text
    assert "acme/gadget" in text


# --- the head gate ------------------------------------------------------------------------------


def _gate(compares: _Compares, head: str = "merge1") -> list[str]:
    return list(asyncio.run(gate_head(_target(DoubleRepo(compares=compares)), head)).offenders)


def test_a_head_equal_to_the_submitted_commit_has_no_offenders() -> None:
    assert _gate({}, head="sha1") == []


def test_an_identical_comparison_has_no_offenders() -> None:
    assert _gate(_compare("sha1", "merge1", status=ComparisonStatus.IDENTICAL)) == []


def test_a_refused_comparison_of_the_head_refuses() -> None:
    assert _gate({}) == ["the forge would not compare sha1...merge1"]


@pytest.mark.parametrize("status", [ComparisonStatus.BEHIND, ComparisonStatus.DIVERGED])
def test_a_head_not_ahead_of_the_submitted_commit_was_rewritten(status: ComparisonStatus) -> None:
    assert _gate(_compare("sha1", "merge1", status=status)) == [
        "merge1 does not descend from the submitted commit sha1 (it was rewritten or reset)"
    ]


def test_a_truncated_commit_list_is_refused() -> None:
    compares = _compare("sha1", "merge1", commits=(_commit("merge1", "sha1"),), commits_truncated=True)

    assert _gate(compares) == ["the commit list between sha1 and merge1 is truncated"]


def test_a_non_merge_commit_on_the_chain_is_named_as_an_offender() -> None:
    assert _gate(_compare("sha1", "merge1", commits=(_commit("merge1", "sha1"),))) == [
        "merge1 (not a merge of the base branch)"
    ]


def test_an_octopus_merge_is_not_a_base_merge() -> None:
    compares = _compare("sha1", "merge1", commits=(_commit("merge1", "sha1", "b1", "b2"),))

    assert _gate(compares) == ["merge1 (not a merge of the base branch)"]


def test_every_merge_whose_second_parent_the_base_lacks_is_listed() -> None:
    compares = {
        **_compare("sha1", "merge2", commits=(_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2"))),
        **_compare("main", "b1", status=ComparisonStatus.DIVERGED),
        **_compare("main", "b2", status=ComparisonStatus.AHEAD),
    }

    assert _gate(compares, head="merge2") == [
        "merge2 (merges b2, which the base branch does not hold)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


@pytest.mark.parametrize("unreadable", ["merged", "base-side"])
def test_a_one_sided_unreadable_compare_refuses_instead_of_crashing(unreadable: str) -> None:
    files = (_file("a.txt", "blob1"),)
    compares = {
        **_compare("sha1", "merge2", commits=(_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2"))),
        **_compare("main", "b1", status=ComparisonStatus.BEHIND),
        **_compare("main", "b2", status=ComparisonStatus.BEHIND),
        **_compare("sha1", "merge1", files=files),
        **_compare("sha1", "b1", files=files),
        **_compare("mb0", "sha1"),
    }
    if unreadable == "merged":
        compares |= _compare("merge1", "b2", files=files)
    else:
        compares |= _compare("merge1", "merge2", files=files)

    assert _gate(compares, head="merge2") == [
        "merge2 (its content could not be compared with the base branch's change)"
    ]


def test_a_merge_adding_exactly_the_base_change_is_admitted() -> None:
    files = (_file("a.txt", "blob1"),)

    assert _gate(_base_merge(merged_files=files, base_files=files)) == []


def test_a_merge_adding_content_beyond_the_base_change_is_named() -> None:
    merged = (_file("a.txt", "m", "@@ -1 +1 @@\n-a\n+z"),)
    base_side = (_file("a.txt", "b", "@@ -1 +1 @@\n-a\n+b"),)

    assert _gate(_base_merge(merged_files=merged, base_files=base_side)) == [
        "merge1 (adds content beyond the base branch's change)"
    ]


def test_a_missing_merge_base_refuses_the_merge() -> None:
    files = (_file("a.txt", "b"),)
    compares = _base_merge(merged_files=files, base_files=files)
    compares[("sha1", "b1")] = replace(compares[("sha1", "b1")], merge_base=None)

    assert _gate(compares) == ["merge1 (its content could not be compared with the base branch's change)"]


def test_a_refused_feature_side_compare_refuses_the_merge() -> None:
    files = (_file("a.txt", "b"),)
    compares = _base_merge(merged_files=files, base_files=files)
    del compares[("mb0", "sha1")]

    assert _gate(compares) == ["merge1 (its content could not be compared with the base branch's change)"]


def test_a_forge_that_does_not_answer_raises_out_of_the_gate() -> None:
    files = (_file("a.txt", "b"),)
    compares = _base_merge(merged_files=files, base_files=files)

    def compare(base: str, head: str) -> Comparison:
        if (base, head) == ("mb0", "sha1"):
            raise ForgeUnavailable("server error")
        return compares[(base, head)]

    state = DoubleRepo(replies={"compare": [compare]})
    with pytest.raises(ForgeUnavailable):
        asyncio.run(gate_head(_target(state), "merge1"))


def test_a_non_merge_does_not_hide_the_merges_below_it() -> None:
    compares = {
        **_compare("sha1", "merge2", commits=(_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1"))),
        **_compare("main", "b1", status=ComparisonStatus.DIVERGED),
    }

    assert _gate(compares, head="merge2") == [
        "merge2 (not a merge of the base branch)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


def test_an_unprovable_merge_does_not_hide_the_merges_below_it() -> None:
    compares = {
        **_compare("sha1", "merge2", commits=(_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2"))),
        **_compare("main", "b1", status=ComparisonStatus.DIVERGED),
        **_compare("main", "b2", status=ComparisonStatus.BEHIND),
        **_compare("merge1", "b2"),
    }

    assert _gate(compares, head="merge2") == [
        "merge2 (its content could not be compared with the base branch's change)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


# --- the first-parent walk ----------------------------------------------------------------------


def _chain(commits: list[CommitSummary], head: str = "c1") -> tuple[list[CommitSummary], str | None]:
    return first_parent_chain(tuple(commits), head, "sub")


def test_a_chain_as_long_as_the_listed_commits_is_walked() -> None:
    chain, why = _chain([_commit("c2", "c1"), _commit("c1", "sub")], head="c2")

    assert why is None
    assert [c.revision for c in chain] == ["c2", "c1"]


def test_a_looping_first_parent_walk_stops_with_a_reason() -> None:
    _, why = _chain([_commit("c1", "c2"), _commit("c2", "c1")])

    assert why is not None and why.endswith("has no readable first parent")


def test_a_parentless_commit_is_unreachable() -> None:
    _, why = _chain([_commit("c1")])

    assert why == "c1 is not reachable from sub within the listed commits"


def test_a_head_missing_from_the_listed_commits_is_unreachable() -> None:
    _, why = _chain([_commit("c2", "sub")])

    assert why == "c1 is not reachable from sub within the listed commits"


# --- the base tip and the forge's escalations ---------------------------------------------------


def test_a_base_tip_is_the_base_branchs_live_tip() -> None:
    assert asyncio.run(_target(DoubleRepo(base_tip="tip1")).base_tip()) == "tip1"


def test_a_missing_base_branch_has_no_known_tip() -> None:
    with pytest.raises(LandedRevisionUnknown):
        asyncio.run(_target(DoubleRepo(base_tip=None)).base_tip())


def test_a_refused_base_tip_read_is_unknown() -> None:
    state = DoubleRepo(replies={"branch_tip": [ForgeRejected("Bad Request")]})

    with pytest.raises(LandedRevisionUnknown):
        asyncio.run(_target(state).base_tip())


@pytest.mark.parametrize(
    "refusal",
    [ForgeRejected(ForgeRejected.AUTH), ForgeNotFound(ForgeObject.REPOSITORY)],
    ids=["rejected-credential", "unknown-repository"],
)
def test_a_rejected_credential_or_unknown_repository_escalates(refusal: Exception) -> None:
    state = DoubleRepo(replies={"compare": [refusal]})

    with pytest.raises(_Escalation):
        asyncio.run(_target(state).compare("main", "sha1"))


# --- the already-merged change lookup -----------------------------------------------------------


def _merged(number: int, sha: str) -> Change:
    return change(_REPO, number, branch=_BRANCH, sha=sha, base="main", state=ChangeState.MERGED)


def _merged_for(
    *changes: Change, commit: str, tip: str | None = None, replies: dict[str, list[object]] | None = None
) -> Change | None:
    state = DoubleRepo(
        changes={c.number: c for c in changes},
        heads={_BRANCH: tip} if tip is not None else {},
        replies=dict(replies or {}),
    )
    return asyncio.run(_PullRequest._merged_for_branch(_target(state, commit=commit)))


def test_a_stale_merged_change_on_a_reused_branch_name_is_not_the_runs_landing() -> None:
    assert _merged_for(_merged(1, "stale-head-sha"), commit="current-head-sha", tip="current-head-sha") is None


def test_a_closed_unmerged_change_is_not_the_runs_landing() -> None:
    closed = replace(_merged(1, "the-head-sha"), state=ChangeState.CLOSED)

    assert _merged_for(closed, commit="the-head-sha") is None


def test_a_merged_change_matching_the_live_branch_tip_is_the_runs_landing() -> None:
    found = _merged_for(_merged(1, "the-head-sha"), commit="other-sha", tip="the-head-sha")

    assert found is not None and found.number == 1


def test_a_merged_change_recording_the_submitted_commit_is_found_after_the_forge_deleted_its_branch() -> None:
    found = _merged_for(_merged(1, "the-head-sha"), commit="the-head-sha")

    assert found is not None and found.number == 1


def test_a_merged_change_with_another_sha_and_a_deleted_branch_is_not_a_match() -> None:
    assert _merged_for(_merged(1, "an-older-sha"), commit="the-head-sha") is None


def test_a_refused_closed_changes_read_raises_rather_than_reading_as_nothing_merged() -> None:
    with pytest.raises(PullRequestLookupError):
        _merged_for(commit="current-head-sha", replies={"changes": [ForgeRejected("Bad Request")]})


def test_a_refused_live_tip_read_raises_rather_than_reading_as_no_match() -> None:
    with pytest.raises(PullRequestLookupError):
        _merged_for(
            _merged(1, "some-sha"), commit="current-head-sha", replies={"branch_tip": [ForgeRejected("Bad Request")]}
        )


# --- the base-merge proof -----------------------------------------------------------------------


def _files(*files: FileChange, truncated: bool = False) -> Comparison:
    return comparison(files=files, files_truncated=truncated)


def _matches(merged: list[FileChange], base_side: list[FileChange], feature: list[FileChange] | None = None) -> bool:
    return base_merge.contributes_only_base_change(_files(*merged), _files(*base_side), _files(*(feature or [])))


def test_the_same_blob_matches_even_when_the_patches_differ() -> None:
    assert _matches([_file("a.txt", "blob1", "@@ -1 +1 @@\n-a\n+b")], [_file("a.txt", "blob1", "@@ -1 +1 @@\n-x\n+y")])


def test_a_missing_blob_never_counts_as_the_same_blob() -> None:
    assert not _matches([_file("a.txt", None)], [_file("a.txt", None)])


def test_an_unmade_rename_is_a_different_contribution() -> None:
    merged = [_file("b.txt", "blob1", previous="a.txt")]
    base_side = [_file("b.txt", "blob1")]

    assert not _matches(merged, base_side)
    assert not _matches(base_side, merged)


def test_the_same_rename_on_both_sides_matches() -> None:
    renamed = [_file("b.txt", "blob1", previous="a.txt")]

    assert _matches(renamed, renamed)


def test_a_file_set_difference_is_a_different_contribution() -> None:
    assert not _matches([_file("a.txt", "b1")], [_file("b.txt", "b1")])


@pytest.mark.parametrize("which", [0, 1, 2])
def test_a_truncated_file_list_on_any_side_refuses(which: int) -> None:
    files = (_file("a.txt", "b"),)
    sides = [_files(*files), _files(*files), _files()]
    sides[which] = replace(sides[which], files_truncated=True)

    assert not base_merge.contributes_only_base_change(*sides)


def test_a_truncated_file_list_reads_as_no_files() -> None:
    assert base_merge.files_by_name(_files(_file("a", "x"))) == {"a": _file("a", "x")}
    assert base_merge.files_by_name(_files(_file("a", "x"), truncated=True)) is None


def test_matching_multisets_at_a_different_position_are_refused() -> None:
    feature = [_file("a.txt", "f", "@@ -1 +1 @@\n-top\n+TOP")]
    base_side = [_file("a.txt", "b", "@@ -8 +8 @@\n-bottom\n+BOTTOM")]
    moved = [_file("a.txt", "m", "@@ -3 +3 @@\n-bottom\n+BOTTOM")]
    placed = [_file("a.txt", "m", "@@ -8 +8 @@\n-bottom\n+BOTTOM")]

    assert not _matches(moved, base_side, feature)
    assert _matches(placed, base_side, feature)


def test_a_block_is_translated_through_the_feature_sides_line_shift() -> None:
    feature = [_file("a.txt", "f", "@@ -1 +1,3 @@\n-top\n+one\n+two\n+three")]
    base_side = [_file("a.txt", "b", "@@ -8 +8 @@\n-bottom\n+BOTTOM")]

    assert _matches([_file("a.txt", "m", "@@ -10 +10 @@\n-bottom\n+BOTTOM")], base_side, feature)
    assert not _matches([_file("a.txt", "m", "@@ -8 +8 @@\n-bottom\n+BOTTOM")], base_side, feature)


def test_the_base_blob_taken_wholesale_over_a_file_the_feature_changed_is_refused() -> None:
    assert not _matches([_file("a.txt", "b")], [_file("a.txt", "b")], [_file("a.txt", "f")])


@pytest.mark.parametrize("gap", [0, 1])
def test_a_base_block_overlapping_or_abutting_a_feature_block_is_refused(gap: int) -> None:
    feature = [_file("a.txt", "f", "@@ -3 +3 @@\n-x\n+X")]
    base_side = [_file("a.txt", "b", f"@@ -{3 + gap} +{3 + gap} @@\n-y\n+Y")]
    merged = [_file("a.txt", "m", f"@@ -{3 + gap} +{3 + gap} @@\n-y\n+Y")]

    assert not _matches(merged, base_side, feature)


def test_a_base_block_one_line_clear_of_a_feature_block_is_admitted() -> None:
    feature = [_file("a.txt", "f", "@@ -3 +3 @@\n-x\n+X")]
    patch = "@@ -5 +5 @@\n-y\n+Y"

    assert _matches([_file("a.txt", "m", patch)], [_file("a.txt", "b", patch)], feature)


def test_the_no_newline_marker_is_content() -> None:
    feature = [_file("a.txt", "f", "@@ -1 +1 @@\n-top\n+TOP")]
    base_side = [_file("a.txt", "b", "@@ -8 +8 @@\n-bottom\n+BOTTOM\n\\ No newline at end of file")]
    merged = [_file("a.txt", "m", "@@ -8 +8 @@\n-bottom\n+BOTTOM")]

    assert not _matches(merged, base_side, feature)


@pytest.mark.parametrize(
    "tamper",
    [
        lambda f: replace(f, patch=None),
        lambda f: replace(f, additions=f.additions + 1),
        lambda f: replace(f, deletions=f.deletions + 1),
        lambda f: replace(f, patch="@@ -1,3 +1 @@\n-a\n+b"),
        lambda f: replace(f, patch="junk"),
        lambda f: replace(f, previous_path="old.txt"),
    ],
    ids=["no-patch", "additions-off", "deletions-off", "header-off", "junk", "rename"],
)
@pytest.mark.parametrize("which", [0, 1, 2])
def test_an_incomplete_comparison_of_a_file_both_sides_changed_is_refused(
    tamper: Callable[[FileChange], FileChange], which: int
) -> None:
    sides = [[_file("a.txt", "m")], [_file("a.txt", "b")], [_file("a.txt", "f", "@@ -5 +5 @@\n-x\n+X")]]
    sides[which][0] = tamper(sides[which][0])

    assert not _matches(sides[0], sides[1], sides[2])


def test_a_base_rename_of_a_file_the_feature_edited_is_refused() -> None:
    feature = [_file("x.txt", "f")]
    renamed = _file("y.txt", "Y", "@@ -1 +1 @@\n-a\n+b", previous="x.txt")
    reverted = _file("y.txt", "Y", "", previous="x.txt")

    assert not _matches([reverted], [renamed], feature)
    assert not _matches([renamed], [renamed], feature)


# --- patch parsing and translation, driven directly ---------------------------------------------

_Block = base_merge.Block


def _patch_file(patch: str | None, additions: int, deletions: int) -> FileChange:
    return FileChange(
        path="a.txt",
        previous_path=None,
        status="modified",
        blob="s",
        patch=patch,
        additions=additions,
        deletions=deletions,
    )


def test_a_no_newline_marker_after_context_opens_no_change_block() -> None:
    patch = "@@ -1,2 +1,2 @@\n-a\n+b\n tail\n\\ No newline at end of file"
    blocks = base_merge.parse_blocks(_patch_file(patch, 1, 1))

    assert blocks is not None
    assert [(b.start, b.lines) for b in blocks] == [(1, ("-a", "+b"))]


def test_a_patch_parses_into_blocks_positioned_in_old_file_lines() -> None:
    patch = "@@ -1,7 +1,8 @@\n a\n-b\n+B\n+B2\n c\n d\n+e\n f\n-g\n h\n@@ -20,2 +21,3 @@\n x\n+y\n z\n"

    assert base_merge.parse_blocks(_patch_file(patch, 4, 2)) == [
        _Block(2, 1, 2, ("-b", "+B", "+B2")),
        _Block(5, 0, 1, ("+e",)),
        _Block(6, 1, 0, ("-g",)),
        _Block(21, 0, 1, ("+y",)),
    ]


def test_runs_ending_a_hunk_accumulate_into_the_totals_across_hunks() -> None:
    patch = "@@ -1,2 +0,0 @@\n-a\n-b\n@@ -9 +8,2 @@\n-c\n+d\n+e"

    assert base_merge.parse_blocks(_patch_file(patch, 2, 3)) == [
        _Block(1, 2, 0, ("-a", "-b")),
        _Block(9, 1, 2, ("-c", "+d", "+e")),
    ]


def test_a_run_ending_the_last_hunk_adds_to_the_totals_of_earlier_runs() -> None:
    patch = "@@ -1,2 +1,2 @@\n-a\n+b\n c\n@@ -9 +9 @@\n-d\n+e"

    assert base_merge.parse_blocks(_patch_file(patch, 2, 2)) == [
        _Block(1, 1, 1, ("-a", "+b")),
        _Block(9, 1, 1, ("-d", "+e")),
    ]


def test_a_single_line_hunk_header_means_a_count_of_one() -> None:
    assert base_merge.parse_blocks(_patch_file("@@ -5 +5 @@\n-a\n+b", 1, 1)) == [_Block(5, 1, 1, ("-a", "+b"))]


def test_a_pure_insertion_hunk_starts_after_the_old_line_it_names() -> None:
    assert base_merge.parse_blocks(_patch_file("@@ -3,0 +4,2 @@\n+x\n+y", 2, 0)) == [_Block(4, 0, 2, ("+x", "+y"))]


def test_a_pure_deletion_is_a_valid_count_of_zero_additions() -> None:
    assert base_merge.parse_blocks(_patch_file("@@ -2 +1,0 @@\n-x", 0, 1)) == [_Block(2, 1, 0, ("-x",))]


def test_the_no_newline_marker_is_content_of_its_block() -> None:
    patch = "@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n\\ No newline at end of file\n"

    assert base_merge.parse_blocks(_patch_file(patch, 1, 1)) == [
        _Block(1, 1, 1, ("-a", "\\ No newline at end of file", "+b", "\\ No newline at end of file"))
    ]


@pytest.mark.parametrize(
    ("patch", "additions", "deletions"),
    [
        ("@@ -1,2 +1,2 @@\n-a\n+b", 1, 1),
        ("@@ -1 +1 @@\n-a\n+b\n", 2, 1),
        ("@@ -1 +1 @@\n-a\n+b\n", 1, 2),
        ("@@ -1 +1 @@\n-a\n+b\n?odd", 1, 1),
        ("not a hunk\n-a\n+b", 1, 1),
        ("@@ -1 +1 @@\n-a\n+b\n@@ -9 +9 @@\n-c\n+d", 1, 1),
        ("@@ -1 +1 @@\n-a\n+b", True, 1),
        (None, 1, 1),
    ],
    ids=[
        "header-counts-disagree",
        "additions-disagree",
        "deletions-disagree",
        "unknown-line-kind",
        "no-hunk-header",
        "later-hunk-totals-disagree",
        "bool-count",
        "no-patch",
    ],
)
def test_a_patch_that_cannot_be_trusted_complete_parses_to_nothing(
    patch: str | None, additions: int, deletions: int
) -> None:
    assert base_merge.parse_blocks(_patch_file(patch, additions, deletions)) is None


def test_a_translation_accumulates_the_shift_of_every_feature_block_before_it() -> None:
    feature = [_Block(1, 1, 3, ("-a",)), _Block(5, 0, 2, ("+b",))]

    assert base_merge._translate([_Block(20, 1, 1, ("-c", "+d"))], feature) == [_Block(24, 1, 1, ("-c", "+d"))]


def test_a_base_block_abutting_a_feature_block_is_not_translated() -> None:
    assert base_merge._translate([_Block(3, 2, 1, ())], [_Block(5, 1, 1, ())]) is None
    assert base_merge._translate([_Block(6, 1, 1, ())], [_Block(5, 1, 1, ())]) is None
    assert base_merge._translate([_Block(3, 1, 1, ())], [_Block(5, 1, 1, ())]) == [_Block(3, 1, 1, ())]
