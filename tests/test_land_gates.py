"""What ``land`` decides from forge payloads alone: its check-run verdicts and findings, the head gate's
first-parent walk and offenders, the base-tip read, the already-merged PR lookup, and the base-merge proof
(unit tier)."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest

from blizzard.hub.delivery.actions import base_merge
from blizzard.hub.delivery.actions.land import (
    ForgeReadDegraded,
    LandedRevisionUnknown,
    PullRequestLookupError,
    _PullRequest,
    _Run,
    _Target,
    gate_head,
)
from blizzard.hub.delivery.actions.land_checks import FAILED, WAIT, Findings, Verdict, first_parent_chain
from blizzard.hub.delivery.forge_handle import ForgeReply, IGitHubHandle
from blizzard.hub.domain.config.repositories import ResolvedRepository

pytestmark = pytest.mark.unit

_REPO = "acme/widget"

_Responses = dict[tuple[str, str], tuple[int, Any]]


class _Forge:
    """The reads the head gate, the base-tip read, and the merged-PR lookup make, answered from ``responses`` by URL."""

    def __init__(self, responses: _Responses) -> None:
        self._responses = responses

    async def compare(self, repo: str, base: str, head: str) -> ForgeReply:
        return ForgeReply(*self._responses[("GET", f"http://forge/repos/{repo}/compare/{base}...{head}")])

    async def closed_pulls(self, repo: str, *, base: str, page: int) -> ForgeReply:
        key = ("GET", f"http://forge/repos/{repo}/pulls?state=closed&base={base}&page={page}&per_page=100")
        return ForgeReply(*self._responses[key])

    async def branch(self, repo: str, branch: str) -> ForgeReply:
        return ForgeReply(*self._responses[("GET", f"http://forge/repos/{repo}/git/ref/heads/{branch}")])


def _target(responses: _Responses, *, commit: str = "sha1") -> _Target:
    record = ResolvedRepository(
        forge_api_url="http://forge", owner="acme", base_branch="main", secret_name="s", record="re_widget"
    )
    return _Target(
        run=cast(_Run, None),
        repo=_REPO,
        branch="feature-branch",
        commit=commit,
        record=record,
        forge=cast(IGitHubHandle, _Forge(responses)),
    )


def _compare(
    base: str,
    head: str,
    *,
    status: str = "ahead",
    commits: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    total: int | None = None,
) -> _Responses:
    commits = commits or []
    payload = {
        "status": status,
        "ahead_by": len(commits) if total is None else total,
        "merge_base_commit": {"sha": "mb0"},
        "commits": commits,
        "files": files or [],
    }
    return {("GET", f"http://forge/repos/{_REPO}/compare/{base}...{head}"): (200, payload)}


def _commit(sha: str, *parents: str) -> dict[str, Any]:
    return {"sha": sha, "parents": [{"sha": p} for p in parents]}


def _file(name: str, blob: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b") -> dict[str, Any]:
    body = (patch or "").split("\n")
    return {
        "filename": name,
        "status": "modified",
        "sha": blob,
        "patch": patch,
        "additions": sum(line.startswith("+") for line in body),
        "deletions": sum(line.startswith("-") for line in body),
    }


def _base_merge(
    *,
    merged_files: list[dict[str, Any]],
    base_files: list[dict[str, Any]],
    feature_files: list[dict[str, Any]] | None = None,
) -> _Responses:
    """A live head ``merge1``: the submitted ``sha1`` with base commit ``b1`` merged in;
    ``feature_files`` is what the feature side changed since the merge base ``mb0``."""
    return {
        **_compare("mb0", "sha1", files=feature_files),
        **_compare(
            "sha1", "merge1", commits=[_commit("b1", "base0"), _commit("merge1", "sha1", "b1")], files=merged_files
        ),
        **_compare("main", "b1", status="behind"),
        **_compare("sha1", "b1", files=base_files),
    }


# Network-free CI verdict and findings checks.


def _check_run(status: str, conclusion: str | None = None, *, name: str = "build", check_id: int = 1) -> dict[str, Any]:
    return {
        "id": check_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "details_url": f"https://forge/{name}/{check_id}",
        "head_sha": "sha1",
    }


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "action_required"])
def test_verdict_is_failed_for_every_terminal_conclusion(conclusion: str) -> None:
    assert Verdict.of(200, {"check_runs": [_check_run("completed", conclusion)]}).decision == FAILED


def test_verdict_waits_on_a_cancelled_conclusion() -> None:
    """A cancellation is re-polled because it may come from a concurrency group."""
    assert Verdict.of(200, {"check_runs": [_check_run("completed", "cancelled")]}).decision == WAIT


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested"])
def test_verdict_waits_for_every_non_terminal_status(status: str) -> None:
    assert Verdict.of(200, {"check_runs": [_check_run(status)]}).decision == WAIT


def test_verdict_waits_on_an_empty_list() -> None:
    assert Verdict.of(200, {"check_runs": []}).decision == WAIT


@pytest.mark.parametrize(
    "check_runs",
    [
        [{"name": "build"}],  # missing status/conclusion
        [{"status": "completed"}],  # missing conclusion
        [{"status": "completed", "conclusion": None}],  # conclusion not yet set
        "not-a-list",  # wrong top-level type
        [None],  # non-dict entry
    ],
    ids=["missing-status-and-conclusion", "missing-conclusion", "null-conclusion", "wrong-type", "non-dict-entry"],
)
def test_verdict_degrades_to_wait_on_a_malformed_payload_without_raising(check_runs: Any) -> None:
    assert Verdict.of(200, {"check_runs": check_runs}).decision == WAIT


def test_verdict_fails_when_any_run_among_several_is_terminal() -> None:
    runs = [_check_run("completed", "success"), _check_run("in_progress"), _check_run("completed", "failure")]
    assert Verdict.of(200, {"check_runs": runs}).decision == FAILED


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


def _gate_offenders(responses: _Responses, head: str = "merge1") -> list[str]:
    return list(asyncio.run(gate_head(_target(responses), head)).offenders)


def _unreadable_compare(base: str, head: str) -> _Responses:
    return {("GET", f"http://forge/repos/{_REPO}/compare/{base}...{head}"): (404, {"message": "Not Found"})}


def test_a_non_merge_commit_on_the_chain_is_named_as_an_offender() -> None:
    responses = _compare("sha1", "merge1", commits=[_commit("merge1", "sha1")])

    assert _gate_offenders(responses) == ["merge1 (not a merge of the base branch)"]


@pytest.mark.parametrize(
    "parents",
    [
        [{"sha": "sha1"}, {"sha": "b1"}, {"sha": "b2"}],
        [{"sha": "sha1"}, {"sha": "b1"}, "junk"],
        [{"sha": "sha1"}, {"sha": 7}],
    ],
    ids=["octopus", "two-valid-plus-malformed", "malformed-second-parent"],
)
def test_a_commit_without_exactly_two_readable_parents_is_not_a_base_merge(parents: list[Any]) -> None:
    responses = _compare("sha1", "merge1", commits=[{"sha": "merge1", "parents": parents}])

    assert _gate_offenders(responses) == ["merge1 (not a merge of the base branch)"]


def test_every_merge_whose_second_parent_the_base_lacks_is_listed() -> None:
    responses = {
        **_compare(
            "sha1",
            "merge2",
            commits=[_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2")],
        ),
        **_compare("main", "b1", status="diverged"),
        **_compare("main", "b2", status="ahead"),
    }

    assert _gate_offenders(responses, head="merge2") == [
        "merge2 (merges b2, which the base branch does not hold)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


@pytest.mark.parametrize("unreadable", ["merged", "base-side"])
def test_a_one_sided_unreadable_compare_refuses_instead_of_crashing(unreadable: str) -> None:
    files = [_file("a.txt", "blob1")]
    responses = {
        **_compare("sha1", "merge2", commits=[_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2")]),
        **_compare("main", "b1", status="behind"),
        **_compare("main", "b2", status="behind"),
        **_compare("sha1", "merge1", files=files),
        **_compare("sha1", "b1", files=files),
        **_compare("mb0", "sha1"),
        **_compare("mb0", "merge1"),
    }
    if unreadable == "merged":
        responses |= _unreadable_compare("merge1", "merge2") | _compare("merge1", "b2", files=files)
    else:
        responses |= _compare("merge1", "merge2", files=files) | _unreadable_compare("merge1", "b2")

    assert _gate_offenders(responses, head="merge2") == [
        "merge2 (its content could not be compared with the base branch's change)"
    ]


def test_a_merge_adding_exactly_the_base_change_is_admitted() -> None:
    files = [_file("a.txt", "blob1")]

    assert _gate_offenders(_base_merge(merged_files=files, base_files=files)) == []


def _matches(
    merged: list[dict[str, Any]], base_side: list[dict[str, Any]], feature: list[dict[str, Any]] | None = None
) -> bool:
    return base_merge.contributes_only_base_change({"files": merged}, {"files": base_side}, {"files": feature or []})


def test_the_same_blob_matches_even_when_the_patches_differ() -> None:
    merged = [_file("a.txt", "blob1", "@@ -1 +1 @@\n-a\n+b")]
    base_side = [_file("a.txt", "blob1", "@@ -1 +1 @@\n-x\n+y")]

    assert _matches(merged, base_side)


def test_a_missing_blob_sha_never_counts_as_the_same_blob() -> None:
    merged = [_file("a.txt", "blob1", "@@ -1 +1 @@\n-a\n+b")]
    base_side = [_file("a.txt", "blob2", "@@ -1 +1 @@\n-x\n+y")]
    for file in (*merged, *base_side):
        file["sha"] = None

    assert not _matches(merged, base_side)


def test_an_unmade_rename_is_a_different_contribution() -> None:
    merged = [{**_file("b.txt", "blob1"), "previous_filename": "a.txt"}]
    base_side = [_file("b.txt", "blob1")]

    assert not _matches(merged, base_side)
    assert not _matches(base_side, merged)


def test_the_same_rename_on_both_sides_matches() -> None:
    renamed = [{**_file("b.txt", "blob1"), "previous_filename": "a.txt"}]

    assert _matches(renamed, renamed)


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
    feature = [_file("a.txt", "f")]
    base_side = [_file("a.txt", "b")]

    assert not _matches([_file("a.txt", "b")], base_side, feature)


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
        lambda f: f.pop("patch"),
        lambda f: f.pop("additions"),
        lambda f: f.pop("deletions"),
        lambda f: f.update(additions=f["additions"] + 1),
        lambda f: f.update(deletions=f["deletions"] + 1),
        lambda f: f.update(patch="@@ -1,3 +1 @@\n-a\n+b"),
        lambda f: f.update(patch="junk"),
        lambda f: f.update(previous_filename="old.txt"),
    ],
    ids=["no-patch", "no-additions", "no-deletions", "additions-off", "deletions-off", "header-off", "junk", "rename"],
)
@pytest.mark.parametrize("which", [0, 1, 2])
def test_an_incomplete_comparison_of_a_file_both_sides_changed_is_refused(tamper: Any, which: int) -> None:
    sides = [[_file("a.txt", "m")], [_file("a.txt", "b")], [_file("a.txt", "f", "@@ -5 +5 @@\n-x\n+X")]]
    tamper(sides[which][0])

    assert not _matches(sides[0], sides[1], sides[2])


def test_a_truncated_or_missing_merge_base_side_refuses() -> None:
    files = [_file("a.txt", "b")]

    assert not base_merge.contributes_only_base_change(
        {"files": files}, {"files": files}, {"files": [_file(f"f{i}", "x") for i in range(300)]}
    )
    assert base_merge.merge_base_of({"merge_base_commit": {"sha": 7}}) is None
    assert base_merge.merge_base_of({}) is None


def test_a_missing_merge_base_refuses_the_merge() -> None:
    responses = _base_merge(merged_files=[_file("a.txt", "b")], base_files=[_file("a.txt", "b")])
    key = ("GET", f"http://forge/repos/{_REPO}/compare/sha1...b1")
    responses[key] = (200, {k: v for k, v in responses[key][1].items() if k != "merge_base_commit"})

    assert _gate_offenders(responses) == ["merge1 (its content could not be compared with the base branch's change)"]


def test_an_unreadable_feature_side_compare_refuses_and_a_degraded_one_pends() -> None:
    responses = _base_merge(merged_files=[_file("a.txt", "b")], base_files=[_file("a.txt", "b")])
    key = ("GET", f"http://forge/repos/{_REPO}/compare/mb0...sha1")
    responses[key] = (404, {"message": "Not Found"})
    assert _gate_offenders(responses) == ["merge1 (its content could not be compared with the base branch's change)"]

    responses[key] = (503, {})
    with pytest.raises(ForgeReadDegraded):
        _gate_offenders(responses)


def test_a_file_set_difference_is_a_different_contribution() -> None:
    assert not _matches([_file("a.txt", "b1")], [_file("b.txt", "b1")])


def _chain(commits: list[Any], head: str = "c1") -> tuple[list[dict[str, Any]], str | None]:
    return first_parent_chain(commits, head, "sub")


def test_a_chain_as_long_as_the_listed_commits_is_walked() -> None:
    chain, why = _chain([_commit("c2", "c1"), _commit("c1", "sub")], head="c2")

    assert why is None
    assert [c["sha"] for c in chain] == ["c2", "c1"]


def test_a_looping_first_parent_walk_stops_with_a_reason() -> None:
    _, why = _chain([_commit("c1", "c2"), _commit("c2", "c1")])

    assert why is not None and why.endswith("has no readable first parent")


@pytest.mark.parametrize(
    "commit",
    [
        {"sha": "c1", "parents": []},
        {"sha": "c1"},
        {"sha": "c1", "parents": "sub"},
    ],
    ids=["parentless", "no-parents-key", "parents-not-a-list"],
)
def test_a_commit_without_a_parent_list_is_unreachable(commit: dict[str, Any]) -> None:
    _, why = _chain([commit])

    assert why == "c1 is not reachable from sub within the listed commits"


@pytest.mark.parametrize("parents", [["sub"], [{"sha": 3}], [{}]], ids=["not-a-dict", "non-string-sha", "no-sha"])
def test_a_malformed_first_parent_is_unreadable(parents: list[Any]) -> None:
    _, why = _chain([{"sha": "c1", "parents": parents}])

    assert why == "c1 has no readable first parent"


@pytest.mark.parametrize(
    "payload", [{}, {"object": None}, {"object": {}}, {"object": {"sha": ""}}, {"object": {"sha": 7}}]
)
def test_a_base_tip_answered_without_a_usable_sha_is_unknown(payload: dict[str, Any]) -> None:
    target = _target({("GET", f"http://forge/repos/{_REPO}/git/ref/heads/main"): (200, payload)})

    with pytest.raises(LandedRevisionUnknown):
        asyncio.run(target.base_tip())


def test_a_base_tip_with_a_sha_is_returned() -> None:
    target = _target({("GET", f"http://forge/repos/{_REPO}/git/ref/heads/main"): (200, {"object": {"sha": "tip1"}})})

    assert asyncio.run(target.base_tip()) == "tip1"


def test_a_base_rename_of_a_file_the_feature_edited_is_refused() -> None:
    feature = [_file("x.txt", "f")]
    renamed = {**_file("y.txt", "Y", "@@ -1 +1 @@\n-a\n+b"), "previous_filename": "x.txt"}
    reverted = {**_file("y.txt", "Y", ""), "previous_filename": "x.txt"}

    assert not _matches([reverted], [renamed], feature)
    assert not _matches([renamed], [renamed], feature)


def test_a_no_newline_marker_after_context_opens_no_change_block() -> None:
    patch = "@@ -1,2 +1,2 @@\n-a\n+b\n tail\n\\ No newline at end of file"
    blocks = base_merge.parse_blocks(_file("a.txt", "s", patch))

    assert blocks is not None
    assert [(b.start, b.lines) for b in blocks] == [(1, ("-a", "+b"))]


# --- the base-merge proof, driven directly -----------------------------------------------

_Block = base_merge.Block


def _patch_file(patch: str, additions: Any, deletions: Any) -> dict[str, Any]:
    return {"patch": patch, "additions": additions, "deletions": deletions}


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
        ("@@ -1 +1 @@\n-a\n+b", None, 1),
        ("@@ -1 +1 @@\n-a\n+b", 1, None),
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
        "missing-additions",
        "missing-deletions",
        "no-patch",
    ],
)
def test_a_patch_that_cannot_be_trusted_complete_parses_to_nothing(patch: Any, additions: Any, deletions: Any) -> None:
    assert base_merge.parse_blocks(_patch_file(patch, additions, deletions)) is None


def test_a_translation_accumulates_the_shift_of_every_feature_block_before_it() -> None:
    feature = [_Block(1, 1, 3, ("-a",)), _Block(5, 0, 2, ("+b",))]

    assert base_merge._translate([_Block(20, 1, 1, ("-c", "+d"))], feature) == [_Block(24, 1, 1, ("-c", "+d"))]


def test_a_base_block_abutting_a_feature_block_is_not_translated() -> None:
    assert base_merge._translate([_Block(3, 2, 1, ())], [_Block(5, 1, 1, ())]) is None
    assert base_merge._translate([_Block(6, 1, 1, ())], [_Block(5, 1, 1, ())]) is None
    assert base_merge._translate([_Block(3, 1, 1, ())], [_Block(5, 1, 1, ())]) == [_Block(3, 1, 1, ())]


def test_a_files_list_is_refused_when_truncated_or_malformed() -> None:
    assert base_merge.files_by_name({"files": [{"filename": "a"}]}) == {"a": {"filename": "a"}}
    assert base_merge.files_by_name({"files": [{"filename": "a"}] * base_merge.COMPARE_FILES_MAX}) is None
    assert base_merge.files_by_name({"files": [{"filename": 3}]}) is None
    assert base_merge.files_by_name({"files": ["a"]}) is None
    assert base_merge.files_by_name({}) is None


def test_a_merge_whose_own_files_are_unreadable_is_not_proven() -> None:
    base_side = {"files": [_file("a.txt", "b")]}

    assert base_merge.contributes_only_base_change({"files": "x"}, base_side, {"files": []}) is False
    assert base_merge.contributes_only_base_change({"files": []}, {"files": "x"}, {"files": []}) is False
    assert base_merge.contributes_only_base_change({"files": []}, base_side, {"files": "x"}) is False


def _bare_compare(**fields: Any) -> _Responses:
    return {("GET", f"http://forge/repos/{_REPO}/compare/sha1...merge1"): (200, fields)}


def test_a_head_equal_to_the_submitted_commit_has_no_offenders() -> None:
    assert _gate_offenders({}, head="sha1") == []


def test_an_identical_comparison_has_no_offenders() -> None:
    assert _gate_offenders(_compare("sha1", "merge1", status="identical")) == []


def test_the_commit_count_is_read_from_total_commits_before_ahead_by() -> None:
    commits = [_commit("merge1", "sha1")]
    responses = _bare_compare(status="ahead", total_commits=1, ahead_by=99, commits=commits)

    assert _gate_offenders(responses) == ["merge1 (not a merge of the base branch)"]


@pytest.mark.parametrize("commits", [None, "x"], ids=["missing", "not-a-list"])
def test_an_unreadable_commit_list_is_refused(commits: Any) -> None:
    responses = _bare_compare(status="ahead", total_commits=1, commits=commits)

    assert _gate_offenders(responses) == ["the commit list between sha1 and merge1 is truncated or unreadable"]


def test_a_non_merge_does_not_hide_the_merges_below_it() -> None:
    responses = {
        **_compare("sha1", "merge2", commits=[_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1")]),
        **_compare("main", "b1", status="diverged"),
    }

    assert _gate_offenders(responses, head="merge2") == [
        "merge2 (not a merge of the base branch)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


def test_an_unprovable_merge_does_not_hide_the_merges_below_it() -> None:
    responses = {
        **_compare("sha1", "merge2", commits=[_commit("merge1", "sha1", "b1"), _commit("merge2", "merge1", "b2")]),
        **_compare("main", "b1", status="diverged"),
        **_compare("main", "b2", status="behind"),
        **_unreadable_compare("merge1", "merge2"),
        **_compare("merge1", "b2"),
    }

    assert _gate_offenders(responses, head="merge2") == [
        "merge2 (its content could not be compared with the base branch's change)",
        "merge1 (merges b1, which the base branch does not hold)",
    ]


def test_a_merge_adding_content_beyond_the_base_change_is_named() -> None:
    merged = [_file("a.txt", "m", "@@ -1 +1 @@\n-a\n+z")]
    base_side = [_file("a.txt", "b", "@@ -1 +1 @@\n-a\n+b")]

    assert _gate_offenders(_base_merge(merged_files=merged, base_files=base_side)) == [
        "merge1 (adds content beyond the base branch's change)"
    ]


# --- per-repository targets ---------------------------------------------------------------------


# --- the already-merged PR lookup ---------------------------------------------------------------

_BRANCH = "feature-branch"
_PULLS = f"http://forge/repos/{_REPO}"


def _closed(*pulls: dict[str, Any], page: int = 1) -> _Responses:
    return {("GET", f"{_PULLS}/pulls?state=closed&base=main&page={page}&per_page=100"): (200, list(pulls))}


def _merged_pull(number: int, sha: str, *, branch: str = _BRANCH, base: str = "main") -> dict[str, Any]:
    return {
        "number": number,
        "head": {"ref": branch, "sha": sha},
        "base": {"ref": base},
        "merged_at": "2024-01-01T00:00:00Z",
    }


def _tip(status: int, sha: str | None = None) -> _Responses:
    payload: Any = {"object": {"sha": sha}} if sha else {"message": "x"}
    return {("GET", f"{_PULLS}/git/ref/heads/{_BRANCH}"): (status, payload)}


def _merged_for(responses: _Responses, commit: str) -> dict[str, Any] | None:
    return asyncio.run(_PullRequest._merged_for_branch(_target(responses, commit=commit)))


def test_a_stale_merged_pr_on_a_reused_branch_name_is_not_the_runs_landing() -> None:
    responses = {**_closed(_merged_pull(1, "stale-head-sha")), **_tip(200, "current-head-sha")}

    assert _merged_for(responses, "current-head-sha") is None


def test_a_merged_pr_on_the_same_branch_into_another_base_is_not_the_runs_landing() -> None:
    assert _merged_for(_closed(_merged_pull(1, "the-head-sha", base="develop")), "the-head-sha") is None


def test_a_merged_candidate_past_the_first_page_of_closed_pulls_is_still_found() -> None:
    page1 = [_merged_pull(n, f"sha{n}", branch="other-branch") for n in range(1, 101)]
    responses = {**_closed(*page1), **_closed(_merged_pull(101, "the-head-sha"), page=2)}

    assert (_merged_for(responses, "the-head-sha") or {}).get("number") == 101


def test_a_merged_pr_matching_the_live_branch_tip_is_the_runs_landing() -> None:
    responses = {**_closed(_merged_pull(1, "the-head-sha")), **_tip(200, "the-head-sha")}

    assert (_merged_for(responses, "other-sha") or {}).get("number") == 1


def test_a_merged_pr_recording_the_submitted_commit_is_found_after_the_forge_deleted_its_branch() -> None:
    responses = {**_closed(_merged_pull(1, "the-head-sha")), **_tip(404)}

    assert (_merged_for(responses, "the-head-sha") or {}).get("number") == 1


def test_a_merged_pr_with_another_sha_and_a_deleted_branch_is_not_a_match() -> None:
    responses = {**_closed(_merged_pull(1, "an-older-sha")), **_tip(404)}

    assert _merged_for(responses, "the-head-sha") is None


def test_a_degraded_closed_pulls_read_raises_rather_than_reading_as_nothing_merged() -> None:
    responses = {("GET", f"{_PULLS}/pulls?state=closed&base=main&page=1&per_page=100"): (503, {"message": "x"})}

    with pytest.raises(PullRequestLookupError):
        _merged_for(responses, "current-head-sha")


def test_a_degraded_live_tip_read_raises_rather_than_reading_as_no_match() -> None:
    responses = {**_closed(_merged_pull(1, "some-sha")), **_tip(500)}

    with pytest.raises(PullRequestLookupError):
        _merged_for(responses, "current-head-sha")
