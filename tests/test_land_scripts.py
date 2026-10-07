"""Land-script PR titles, merge messages, CI routing and marker writes."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest

from blizzard.hub.graphs.scripts import base_merge, land_common, land_default, land_pr_ci

pytestmark = pytest.mark.unit

_REPO = "acme/widget"
_BRANCH = "feature-branch"
_COMMIT = "sha1"
_COMMITS = [{"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT}]

# Every scripted forge double needs a marker callback response.
_CALLBACK_URL = "http://callback/hub-markers"
_MARKER_TOKEN = "test-marker-token"


def _marker_status_queue(marker_status: int | list[int]) -> tuple[list[int] | None, int]:
    """A list supplies one status per call; an int repeats forever."""
    if isinstance(marker_status, list):
        return list(marker_status), 200
    return None, marker_status


def _next_marker_status(queue: list[int] | None, fallback: int) -> int:
    return queue.pop(0) if queue else fallback


def _scripted_forge(
    calls: list[tuple[str, str, dict[str, Any] | None]],
    *,
    marker_headers: list[dict[str, str] | None] | None = None,
    marker_status: int | list[int] = 200,
):
    """A one-repo clean-merge forge double recording every call and marker response."""
    responses = {
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=open"): (200, []),
        ("POST", f"http://forge/repos/{_REPO}/pulls"): (201, {"number": 1, "head": {"ref": _BRANCH}}),
        ("GET", f"http://forge/repos/{_REPO}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": False,
                "mergeable_state": "clean",
                "head": {"ref": _BRANCH, "sha": "sha1"},
            },
        ),
        ("GET", f"http://forge/repos/{_REPO}/commits/sha1/check-runs"): (
            200,
            {"total_count": 1, "check_runs": [_check_run("completed", "success")]},
        ),
        ("PUT", f"http://forge/repos/{_REPO}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
    }
    marker_queue, marker_fallback = _marker_status_queue(marker_status)

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            if marker_headers is not None:
                marker_headers.append(headers)
            status = _next_marker_status(marker_queue, marker_fallback)
            return status, ({} if 200 <= status < 300 else {"message": "marker write failed"})
        return responses[(method, url)]

    return fake


def _set_base_env(monkeypatch: pytest.MonkeyPatch, *, feature_title: str | None) -> None:
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(_COMMITS))
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_OWNER", raising=False)
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", _CALLBACK_URL)
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", _MARKER_TOKEN)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    if feature_title is None:
        monkeypatch.delenv("BZ_HUB_FEATURE_TITLE", raising=False)
    else:
        monkeypatch.setenv("BZ_HUB_FEATURE_TITLE", feature_title)


def _pr_title(calls: list[tuple[str, str, dict[str, Any] | None]]) -> str:
    body = next(body for method, url, body in calls if method == "POST" and url.endswith("/pulls"))
    assert body is not None
    return body["title"]


def _merge_commit_message(calls: list[tuple[str, str, dict[str, Any] | None]]) -> str:
    body = next(body for method, url, body in calls if method == "PUT" and url.endswith("/merge"))
    assert body is not None
    return body["commit_message"]


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_feature_title_is_used_as_the_pr_title_and_merge_commit_message(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    _set_base_env(monkeypatch, feature_title="Add rate limiting to the widget API")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    assert _pr_title(calls) == "Add rate limiting to the widget API"
    assert _merge_commit_message(calls) == "Add rate limiting to the widget API"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_missing_feature_title_falls_back_to_the_branch_and_land_strings(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    _set_base_env(monkeypatch, feature_title=None)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    assert _pr_title(calls) == _BRANCH
    assert _merge_commit_message(calls) == f"blizzard: land {_REPO}"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_an_over_long_feature_title_is_truncated_for_the_pr_title(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    long_title = "x" * 300
    _set_base_env(monkeypatch, feature_title=long_title)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    title = _pr_title(calls)
    assert len(title) == 256  # GitHub's cap: 255 chars + the ellipsis
    assert title.endswith("…")
    # the merge commit message is a commit body, not a PR title — left untruncated.
    assert _merge_commit_message(calls) == long_title


_CLOSING_KEYWORD = re.compile(r"\b(close[sd]?|fix(e[sd])?|resolve[sd]?)\b", re.IGNORECASE)

_TWO_ITEMS = [
    {"label": "widget#12", "reference": "acme/widget#12"},
    {"label": "hub:7", "reference": None},
]


def _pr_body(calls: list[tuple[str, str, dict[str, Any] | None]]) -> str:
    body = next(body for method, url, body in calls if method == "POST" and url.endswith("/pulls"))
    assert body is not None
    return body["body"]


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_the_pr_body_and_merge_message_name_every_work_item_and_the_chunk(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    _set_base_env(monkeypatch, feature_title="Fix #5 by closing the widget leak")
    monkeypatch.setenv("BZ_HUB_CHUNK_ID", "ch_abc")
    monkeypatch.setenv("BZ_HUB_WORK_ITEMS", json.dumps(_TWO_ITEMS))
    monkeypatch.setenv("BZ_HUB_CHUNK_URL", "https://blizzard.example.com/board/chunk/ch_abc")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    body = _pr_body(calls)
    assert "- Refs acme/widget#12" in body
    assert "- hub:7" in body
    assert "Chunk: ch_abc" in body
    assert "Board: https://blizzard.example.com/board/chunk/ch_abc" in body
    message = _merge_commit_message(calls)
    assert message == "Fix #5 by closing the widget leak\n\nRefs acme/widget#12\nhub:7"
    # The title is the operator's prose; no generated line may form a closing keyword.
    assert not _CLOSING_KEYWORD.search(body)
    assert not _CLOSING_KEYWORD.search(message.split("\n\n", 1)[1])


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
@pytest.mark.parametrize("raw", [None, "not json", '{"label": "x"}', '[{"reference": "a/b#1"}]'])
def test_missing_or_malformed_work_items_degrade_to_a_body_without_items(
    monkeypatch: pytest.MonkeyPatch, module: Any, raw: str | None
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.delenv("BZ_HUB_CHUNK_URL", raising=False)
    monkeypatch.setenv("BZ_HUB_CHUNK_ID", "ch_abc")
    if raw is None:
        monkeypatch.delenv("BZ_HUB_WORK_ITEMS", raising=False)
    else:
        monkeypatch.setenv("BZ_HUB_WORK_ITEMS", raw)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    body = _pr_body(calls)
    assert "Work items" not in body
    assert "Board:" not in body
    assert "Chunk: ch_abc" in body
    assert _merge_commit_message(calls) == "t"


# PR-CI routing: heal behind, wait for CI, bounce on dirty.


def _forge_with_state(
    calls: list[tuple[str, str, dict[str, Any] | None]],
    *,
    mergeable_state: str,
    merged: bool = False,
    update_status: int = 202,
    marker_headers: list[dict[str, str] | None] | None = None,
    marker_status: int | list[int] = 200,
    head_check_runs: list[dict[str, Any]] | None = None,
    head_check_runs_status: int = 200,
    base_check_runs: list[dict[str, Any]] | None = None,
    base_check_runs_status: int = 200,
    rerequest_status: int = 201,
    head_sha: str = "sha1",
    extra_responses: dict[tuple[str, str], tuple[int, Any]] | None = None,
):
    """An open-PR double; unstubbed check routes raise ``KeyError``. Head checks
    with IDs also get a rerequest route."""
    base = f"http://forge/repos/{_REPO}"
    pull = {
        "number": 1,
        "merged": merged,
        "mergeable_state": mergeable_state,
        "head": {"ref": _BRANCH, "sha": head_sha},
        "html_url": f"http://forge/{_REPO}/pull/1",
    }
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": head_sha}}]),
        ("GET", f"{base}/pulls/1"): (200, pull),
        ("PUT", f"{base}/pulls/1/update-branch"): (update_status, {"message": "Updating pull request branch."}),
        ("PUT", f"{base}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
    }
    if head_check_runs is not None:
        responses[("GET", f"{base}/commits/{head_sha}/check-runs")] = (
            head_check_runs_status,
            {"total_count": len(head_check_runs), "check_runs": head_check_runs},
        )
        for check in head_check_runs:
            check_id = check.get("id")
            if check_id is not None:
                responses[("POST", f"{base}/check-runs/{check_id}/rerequest")] = (rerequest_status, {})
    if base_check_runs is not None:
        responses[("GET", f"{base}/commits/main/check-runs")] = (
            base_check_runs_status,
            {"total_count": len(base_check_runs), "check_runs": base_check_runs},
        )
    responses.update(extra_responses or {})
    marker_queue, marker_fallback = _marker_status_queue(marker_status)

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            if marker_headers is not None:
                marker_headers.append(headers)
            status = _next_marker_status(marker_queue, marker_fallback)
            return status, ({"recorded": True} if 200 <= status < 300 else {"message": "marker write failed"})
        return responses[(method, url)]

    return fake


def _urls(calls: list[tuple[str, str, dict[str, Any] | None]], method: str) -> list[str]:
    return [url for m, url, _ in calls if m == method]


def _last_line(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def test_dirty_pr_bounces_conflict_without_merging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_with_state(calls, mergeable_state="dirty"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "conflict"  # the ONE true bounce
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "a dirty PR must not be merged"
    assert not any(url.endswith("/update-branch") for url in _urls(calls, "PUT"))


def test_behind_pr_fires_update_branch_and_pends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_with_state(calls, mergeable_state="behind"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # self-heal in flight, re-poll
    update = [body for m, url, body in calls if m == "PUT" and url.endswith("/update-branch")]
    assert update, "a behind PR must request update-branch"
    assert update[0] == {"expected_head_sha": "sha1"}, "update-branch must guard on the current head"
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "nothing merges while behind"


def test_blocked_pr_pends_without_updating_or_merging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_with_state(calls, mergeable_state="blocked"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # required checks not green — wait, do not bounce
    assert not any(url.endswith(("/merge", "/update-branch")) for url in _urls(calls, "PUT"))


def test_clean_pr_merges_the_current_head_sha(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(calls, mergeable_state="clean", head_check_runs=[_check_run("completed", "success")]),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "landed"
    merge = [body for m, url, body in calls if m == "PUT" and url.endswith("/merge") and body is not None]
    assert merge and merge[0]["sha"] == "sha1"


def _compare(
    base: str,
    head: str,
    *,
    status: str = "ahead",
    commits: list[dict[str, Any]] | None = None,
    files: list[dict[str, Any]] | None = None,
    total: int | None = None,
) -> dict[tuple[str, str], tuple[int, Any]]:
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
) -> dict[tuple[str, str], tuple[int, Any]]:
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


def _gate_forge(
    calls: list[Any], *, head: str, responses: dict[tuple[str, str], tuple[int, Any]], state: str = "clean"
):
    return _forge_with_state(
        calls,
        mergeable_state=state,
        head_sha=head,
        head_check_runs=[_check_run("completed", "success")] if state == "clean" else None,
        extra_responses=responses,
    )


def _no_writes(calls: list[tuple[str, str, dict[str, Any] | None]]) -> bool:
    return not any(url.endswith(("/merge", "/update-branch")) for url in _urls(calls, "PUT"))


def _land_against(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    responses: dict[tuple[str, str], tuple[int, Any]],
    *,
    head: str = "merge1",
    state: str = "clean",
) -> tuple[str, list[tuple[str, str, dict[str, Any] | None]]]:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _gate_forge(calls, head=head, responses=responses, state=state))
    assert land_pr_ci.main() == 0
    return _last_line(capsys), calls


def _findings_text(calls: list[tuple[str, str, dict[str, Any] | None]]) -> str:
    return "\n".join(post["content"] for post in _findings_posts(calls))


def _foreign_findings(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[str]:
    return [
        body["content"]
        for method, url, body in calls
        if method == "POST" and url == _CALLBACK_URL and body and body["name"] == "delivery-findings/foreign-head"
    ]


def test_a_head_advanced_only_by_a_base_merge_lands_the_verified_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = [_file("a.txt", "blob1")]
    outcome, calls = _land_against(monkeypatch, capsys, _base_merge(merged_files=files, base_files=files))

    assert outcome == "landed"
    merge = [body for m, url, body in calls if m == "PUT" and url.endswith("/merge") and body is not None]
    assert merge and merge[0]["sha"] == "merge1", "the verified head, not the submitted commit, is what merges"


def test_a_clean_merge_where_both_sides_changed_disjoint_regions_of_one_file_lands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    feature = [_file("a.txt", "f", "@@ -1,2 +1,2 @@\n-top\n+TOP\n mid")]
    base_side = [_file("a.txt", "b", "@@ -8,2 +8,2 @@\n mid\n-bottom\n+BOTTOM")]
    merged = [_file("a.txt", "m", "@@ -8,2 +8,2 @@\n mid\n-bottom\n+BOTTOM")]
    outcome, _ = _land_against(
        monkeypatch, capsys, _base_merge(merged_files=merged, base_files=base_side, feature_files=feature)
    )

    assert outcome == "landed"


def test_a_foreign_non_merge_commit_on_the_head_refuses_and_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = _compare("sha1", "foreign1", commits=[_commit("foreign1", "sha1")])
    outcome, calls = _land_against(monkeypatch, capsys, responses, head="foreign1")

    assert outcome == "failure"
    assert _no_writes(calls)
    findings = _findings_text(calls)
    assert "foreign1" in findings and "sha1" in findings and _REPO in findings
    assert "CI check failures" not in findings, "a foreign advance must read differently from a CI failure"
    assert _foreign_findings(calls) == [findings], "the same findings ride under the name a prior wait cannot shadow"
    assert not any("/check-runs" in url for _, url, _ in calls), "a foreign head is never read for a verdict"


def test_a_merge_adding_content_beyond_the_base_change_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    merged = [_file("a.txt", "blob1"), _file("smuggled.txt", "blob9")]
    outcome, calls = _land_against(
        monkeypatch, capsys, _base_merge(merged_files=merged, base_files=[_file("a.txt", "blob1")])
    )

    assert outcome == "failure"
    assert _no_writes(calls)
    assert "merge1" in _findings_text(calls)


def test_a_merge_whose_second_parent_the_base_does_not_hold_refuses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = {
        **_compare("sha1", "merge1", commits=[_commit("b1", "base0"), _commit("merge1", "sha1", "b1")]),
        **_compare("main", "b1", status="diverged"),
    }
    outcome, calls = _land_against(monkeypatch, capsys, responses)

    assert outcome == "failure"
    assert _no_writes(calls)


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_a_head_that_no_longer_descends_from_the_submitted_commit_refuses(
    status: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    outcome, calls = _land_against(monkeypatch, capsys, _compare("sha1", "merge1", status=status))

    assert outcome == "failure"
    assert _no_writes(calls)


@pytest.mark.parametrize(
    "responses",
    [
        _compare("sha1", "merge1", commits=[_commit("merge1", "sha1", "b1")], total=2),  # truncated commit list
        _compare("sha1", "merge1", commits=[_commit("other", "sha1")]),  # chain never reaches the head
        {("GET", f"http://forge/repos/{_REPO}/compare/sha1...merge1"): (404, {"message": "Not Found"})},
        {
            **_compare("sha1", "merge1", commits=[_commit("b1", "base0"), _commit("merge1", "sha1", "b1")]),
            **_compare("main", "b1", status="behind"),
            **_compare("mb0", "sha1"),
            **_compare("sha1", "b1", files=[_file("a.txt", "x", patch=None)]),
        },  # a base-side patch the forge did not send
    ],
    ids=["truncated-commits", "unreachable-head", "unanswered-compare", "missing-patch"],
)
def test_an_unprovable_head_refuses(
    responses: dict[tuple[str, str], tuple[int, Any]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outcome, calls = _land_against(monkeypatch, capsys, responses)

    assert outcome == "failure"
    assert _no_writes(calls)


def test_a_truncated_file_list_refuses(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    many = [_file(f"f{i}.txt", f"blob{i}") for i in range(300)]
    outcome, calls = _land_against(monkeypatch, capsys, _base_merge(merged_files=many, base_files=many))

    assert outcome == "failure"
    assert _no_writes(calls)


def test_a_degraded_compare_read_pends_without_writing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    responses = {("GET", f"http://forge/repos/{_REPO}/compare/sha1...merge1"): (502, {"message": "bad gateway"})}
    outcome, calls = _land_against(monkeypatch, capsys, responses)

    assert outcome == "pending"
    assert _no_writes(calls)
    assert not _findings_posts(calls)


def test_a_foreign_head_in_one_repo_fires_nothing_in_a_sibling_that_is_behind(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_repo = "acme/gadget"
    other = f"http://forge/repos/{other_repo}"
    commits = [
        {"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT},
        {"repo": other_repo, "branch": "feat/x", "commit": "osha"},
    ]
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(commits))
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    sibling = {
        ("GET", f"{other}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{other}/pulls?state=open"): (200, [{"number": 2, "head": {"ref": "feat/x", "sha": "osha"}}]),
        ("GET", f"{other}/pulls/2"): (
            200,
            {
                "number": 2,
                "merged": False,
                "mergeable_state": "behind",
                "head": {"ref": "feat/x", "sha": "osha"},
                "html_url": f"http://forge/{other_repo}/pull/2",
            },
        ),
    }
    inner = _gate_forge(
        calls,
        head="foreign1",
        responses={**sibling, **_compare("sha1", "foreign1", commits=[_commit("foreign1", "sha1")])},
    )
    monkeypatch.setattr(land_common, "forge_request", inner)

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "failure"
    assert _no_writes(calls), "no update-branch or merge for ANY repo"


def test_clean_pr_waits_while_its_checks_are_still_pending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`mergeable_state: clean` alone is never enough — the merge path requires a green
    verdict, read independently of branch protection."""
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(calls, mergeable_state="clean", head_check_runs=[_check_run("in_progress", None)]),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "a clean-but-not-green PR must not merge"
    assert any(
        body is not None and body["name"] == f"delivery-pr/{_REPO}/1"
        for method, url, body in calls
        if method == "POST" and url == _CALLBACK_URL
    ), "PR identity must be durable before the CI wait returns"


def test_replacement_pr_has_a_distinct_idempotent_marker_in_the_same_epoch() -> None:
    names: list[str] = []

    def callback(method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        names.append(kwargs["body"]["name"])
        return 200, {}

    run = land_common.LandRun(
        forge_url="http://forge",
        base_branch="main",
        commits=[],
        already=set(),
        markers=land_common.MarkerWriter(_CALLBACK_URL, _MARKER_TOKEN, callback),
    )
    for number in (1, 1, 2):
        pull = land_common.PullRequest(run, _REPO, number, {"html_url": f"http://forge/{_REPO}/pull/{number}"})
        land_common.PullRequest._record(pull)
    assert names == [f"delivery-pr/{_REPO}/1", f"delivery-pr/{_REPO}/1", f"delivery-pr/{_REPO}/2"]


def test_clean_merge_body_requests_a_merge_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A green PR requests a merge commit, preserving its branch history."""
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(calls, mergeable_state="clean", head_check_runs=[_check_run("completed", "success")]),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "landed"
    merge = [body for m, url, body in calls if m == "PUT" and url.endswith("/merge") and body is not None]
    assert merge and merge[0]["merge_method"] == "merge"
    pr_marker = next(
        i
        for i, (_, url, body) in enumerate(calls)
        if url == _CALLBACK_URL and body and body["name"].startswith("delivery-pr/")
    )
    merge_call = next(i for i, (method, url, _) in enumerate(calls) if method == "PUT" and url.endswith("/merge"))
    assert pr_marker < merge_call


# Terminal CI failures and findings.


def _check_runs_urls(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[str]:
    return [url for m, url, _ in calls if m == "GET" and url.endswith("/check-runs")]


def _findings_posts(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]]:
    return [
        body
        for m, url, body in calls
        if m == "POST" and url == _CALLBACK_URL and body is not None and body["name"] == "delivery-findings"
    ]


@pytest.mark.parametrize("state", ["blocked", "unstable"])
def test_a_terminal_check_failure_prints_the_failure_edge_and_writes_findings(
    state: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state=state,
            head_check_runs=[_check_run("completed", "failure")],
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "failure"  # the graph's authored `failure` choice
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT"))
    assert not any(url.endswith("/update-branch") for url in _urls(calls, "PUT"))

    posts = _findings_posts(calls)
    assert len(posts) == 1
    assert posts[0]["name"] == "delivery-findings"
    content = posts[0]["content"]
    assert _REPO in content
    assert "1" in content  # the PR number
    assert "http://forge" in content and "/pull/1" in content  # the PR url
    assert "build" in content  # the check's name
    assert "failure" in content  # the terminal conclusion
    assert "https://forge/build/1" in content  # the check's details_url


def test_a_base_red_check_alongside_an_own_failure_still_fails_and_names_both(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A base-inherited failure does not mask another failing check on the same repo."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[
                _check_run("completed", "failure", name="build", check_id=1),
                _check_run("completed", "failure", name="lint", check_id=2),
            ],
            base_check_runs=[_check_run("completed", "failure", name="build", check_id=1)],
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "failure"
    assert not any(url.endswith("rerequest") for url in _urls(calls, "POST"))

    posts = _findings_posts(calls)
    assert len(posts) == 1
    content = posts[0]["content"]
    assert "not this change" in content  # build: inherited from the base
    assert "this change broke CI" in content  # lint: this chunk's own


def test_an_inherited_only_failure_fires_a_rerequest_once_and_pends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A check failing at the head AND at the base is re-run once
    before anything is charged — not routed as this chunk's own `failure`."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[_check_run("completed", "failure")],
            base_check_runs=[_check_run("completed", "failure")],
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not _findings_posts(calls)

    rerequests = [url for url in _urls(calls, "POST") if url.endswith("/check-runs/1/rerequest")]
    assert rerequests == [f"http://forge/repos/{_REPO}/check-runs/1/rerequest"]

    signature_posts = [
        body
        for m, url, body in calls
        if m == "POST" and url == _CALLBACK_URL and body is not None and body["name"].startswith("ci-rerun/")
    ]
    assert len(signature_posts) == 1
    assert signature_posts[0]["name"] == f"ci-rerun/{_REPO}/build/sha1"


@pytest.mark.parametrize("refusal", [403, 422])
def test_a_refused_rerequest_writes_no_rerun_marker(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], refusal: int
) -> None:
    """A refused rerequest writes no marker, allowing another attempt on the next poll."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[_check_run("completed", "failure")],
            base_check_runs=[_check_run("completed", "failure")],
            rerequest_status=refusal,
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert [url for url in _urls(calls, "POST") if url.endswith("/check-runs/1/rerequest")]
    assert not [
        body
        for m, url, body in calls
        if m == "POST" and url == _CALLBACK_URL and body is not None and body["name"].startswith("ci-rerun/")
    ]


def test_a_re_requested_check_still_red_routes_the_inherited_failure_outcome(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A marked rerequest still red routes inherited-failure without another rerequest."""
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setenv("BZ_HUB_ARTIFACT_NAMES", json.dumps([f"ci-rerun/{_REPO}/build/sha1"]))
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[_check_run("completed", "failure")],
            base_check_runs=[_check_run("completed", "failure")],
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == land_pr_ci._INHERITED_FAILURE
    assert not any(url.endswith("rerequest") for url in _urls(calls, "POST"))
    assert not any(url.endswith(("/merge", "/update-branch")) for url in _urls(calls, "PUT"))

    posts = _findings_posts(calls)
    assert len(posts) == 1
    content = posts[0]["content"]
    assert _REPO in content
    assert "build" in content
    assert "main" in content  # the base branch named
    assert f"ci-rerun/{_REPO}/build/sha1" in content  # the signature, for the loop bound


def test_a_green_re_run_is_simply_not_failing_on_the_next_poll(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setenv("BZ_HUB_ARTIFACT_NAMES", json.dumps([f"ci-rerun/{_REPO}/build/sha1"]))
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(calls, mergeable_state="clean", head_check_runs=[_check_run("completed", "success")]),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "landed"


def test_a_check_runs_read_failure_degrades_to_a_plain_pending_not_the_failure_edge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[_check_run("completed", "failure")],
            head_check_runs_status=500,
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # a forge-read hiccup must never bounce or crash
    assert not _findings_posts(calls)


def test_a_wait_path_findings_write_failure_degrades_to_a_plain_pending_not_a_bounce(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    fake = _forge_with_state(
        calls,
        mergeable_state="blocked",
        head_check_runs=[_check_run("in_progress", None)],
        marker_status=500,  # every delivery-findings write attempt fails
    )

    def only_findings_fail(method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        if url == _CALLBACK_URL and (kwargs.get("body") or {}).get("name", "").startswith("delivery-pr/"):
            calls.append((method, url, kwargs.get("body")))
            return 200, {"recorded": True}
        return fake(method, url, **kwargs)

    monkeypatch.setattr(land_common, "forge_request", only_findings_fail)

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # a hub-side write hiccup must never bounce or crash


def test_two_pending_repos_one_failing_names_only_the_failing_repo_and_merges_neither(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_repo = "acme/gadget"
    other_branch = "other-branch"
    commits = [
        {"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT},
        {"repo": other_repo, "branch": other_branch, "commit": "sha2"},
    ]
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(commits))
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_OWNER", raising=False)
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", _CALLBACK_URL)
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", _MARKER_TOKEN)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    monkeypatch.setenv("BZ_HUB_FEATURE_TITLE", "t")

    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    other_base = f"http://forge/repos/{other_repo}"
    responses = {
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{other_base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=open"): (
            200,
            [{"number": 1, "head": {"ref": _BRANCH, "sha": "sha1"}}],
        ),
        ("GET", f"http://forge/repos/{_REPO}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": False,
                "mergeable_state": "blocked",
                "head": {"ref": _BRANCH, "sha": "sha1"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
        ("GET", f"http://forge/repos/{_REPO}/commits/sha1/check-runs"): (
            200,
            {"total_count": 1, "check_runs": [_check_run("completed", "failure")]},
        ),
        ("GET", f"{other_base}/pulls?state=open"): (
            200,
            [{"number": 2, "head": {"ref": other_branch, "sha": "otherheadsha"}}],
        ),
        ("GET", f"{other_base}/pulls/2"): (
            200,
            {
                "number": 2,
                "merged": False,
                "mergeable_state": "clean",
                "head": {"ref": other_branch, "sha": "otherheadsha"},
                "html_url": f"http://forge/{other_repo}/pull/2",
            },
        ),
    }

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            return 200, {"recorded": True}
        return responses[(method, url)]

    monkeypatch.setattr(land_common, "forge_request", fake)

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "failure"
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "chunk atomicity: neither repo merges"

    posts = _findings_posts(calls)
    assert len(posts) == 1
    content = posts[0]["content"]
    assert _REPO in content
    assert other_repo not in content


def test_an_in_progress_check_writes_exactly_one_wait_finding_and_pends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_state(calls, mergeable_state="blocked", head_check_runs=[_check_run("in_progress", None)]),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"

    posts = _findings_posts(calls)
    assert len(posts) == 1
    content = posts[0]["content"]
    assert _REPO in content
    assert "1" in content  # the PR number
    assert "build" in content  # the check's name
    assert "in_progress" in content  # the check's live status


def test_an_unknown_mergeable_state_never_reads_check_runs_or_writes_findings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(land_common, "forge_request", _forge_with_state(calls, mergeable_state="unknown"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not _check_runs_urls(calls)
    assert not _findings_posts(calls)


def test_an_empty_check_runs_list_is_not_a_substantive_wait(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_common, "forge_request", _forge_with_state(calls, mergeable_state="blocked", head_check_runs=[])
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not _findings_posts(calls)


@pytest.mark.parametrize("script", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_an_empty_commit_set_fails_the_node_instead_of_reporting_landed(
    script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Every land policy filters `commits` through `merged/<repo>` markers before
    deciding `landed` — indistinguishable from "already delivered" unless the empty
    *input* case is caught before the filter."""
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps([]))
    monkeypatch.setenv("BZ_HUB_EXPECT_GIT_COMMITS", "1")  # the graph declared a git_commit
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    monkeypatch.setattr(
        script,
        "forge_request",
        lambda *a, **k: pytest.fail("an empty delivery must never reach the forge"),
        raising=False,
    )

    with pytest.raises(SystemExit) as exc:
        script.main()

    assert exc.value.code == 1  # non-zero: the hub-node protocol's `failure` signal
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "no git commits to deliver" in captured.err


@pytest.mark.parametrize("script", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_a_fully_marked_commit_set_still_reports_landed(
    script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The idempotent re-run the guard must not break: commits exist and every one is
    already marked, so there is genuinely nothing left to do and `landed` is correct."""
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(_COMMITS))
    monkeypatch.setenv("BZ_HUB_EXPECT_GIT_COMMITS", "1")
    monkeypatch.setenv("BZ_HUB_ARTIFACT_NAMES", json.dumps([f"merged/{_REPO}"]))
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    monkeypatch.setattr(
        script,
        "forge_request",
        lambda *a, **k: pytest.fail("an already-marked delivery must not re-contact the forge"),
        raising=False,
    )

    assert script.main() == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "landed"


@pytest.mark.parametrize("script", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_a_non_code_chunk_lands_empty_because_its_graph_promised_no_commit(
    script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MVP criterion 10: a review or a spike declares no `git_commit` anywhere in its
    graph, yet still routes through `deliver` as the uniform terminal — landing nothing
    is its correct outcome, not a defect."""
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps([]))
    monkeypatch.setenv("BZ_HUB_EXPECT_GIT_COMMITS", "0")  # no node declared a git_commit
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    monkeypatch.setattr(
        script,
        "forge_request",
        lambda *a, **k: pytest.fail("a non-code chunk must land without contacting the forge"),
        raising=False,
    )

    assert script.main() == 0
    assert capsys.readouterr().out.strip().splitlines()[-1] == "landed"


@pytest.mark.parametrize("script", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_an_absent_expectation_signal_is_treated_as_expected(script, monkeypatch: pytest.MonkeyPatch) -> None:
    """An older executor injects no signal; failing loudly on a set the policy cannot
    explain is safer than silently assuming "expected"."""
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps([]))
    monkeypatch.delenv("BZ_HUB_EXPECT_GIT_COMMITS", raising=False)
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)

    with pytest.raises(SystemExit) as exc:
        script.main()

    assert exc.value.code == 1


# Durable marker writes shared by both land scripts.


def _forge_double_for(module: Any, calls: list[tuple[str, str, dict[str, Any] | None]], **kwargs: Any):
    """Select a scripted forge double for a fresh or already-open PR."""
    if module is land_pr_ci:
        kwargs.setdefault("head_check_runs", [_check_run("completed", "success")])
        return _forge_with_state(calls, mergeable_state="clean", **kwargs)
    return _scripted_forge(calls, **kwargs)


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_the_marker_post_carries_the_token_header(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    marker_headers: list[dict[str, str] | None] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_double_for(module, calls, marker_headers=marker_headers))

    assert module.main() == 0

    assert marker_headers == [{"X-Blizzard-Marker-Token": _MARKER_TOKEN}] * 2


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_a_non_2xx_marker_write_aborts_without_printing_landed(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_double_for(module, calls, marker_status=401))

    exit_code = module.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "landed" not in captured.err
    assert _REPO in captured.err  # names the failing repo


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_a_503_then_200_on_the_marker_write_retries_exactly_once_then_lands(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_double_for(module, calls, marker_status=[503, 200]))

    assert module.main() == 0

    marker_calls = [c for c in calls if c[1] == _CALLBACK_URL]
    assert len(marker_calls) == 3  # PR reference retries once; merged marker follows
    assert capsys.readouterr().out.strip().splitlines()[-1] == "landed"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_an_unset_forge_url_names_it_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("BZ_FORGE_URL", raising=False)
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(_COMMITS))
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.setattr(
        module, "forge_request", lambda *a, **k: pytest.fail("must not contact the forge"), raising=False
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code != 0
    assert "BZ_FORGE_URL" in capsys.readouterr().err


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_a_chunk_with_no_commits_lands_as_a_no_op_without_any_forge_variable(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("BZ_FORGE_URL", "BZ_FORGE_TOKEN", "BZ_FORGE_OWNER", "BZ_HUB_BASE_BRANCH", "BZ_HUB_ARTIFACT_NAMES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", "[]")
    monkeypatch.setenv("BZ_HUB_EXPECT_GIT_COMMITS", "0")
    monkeypatch.setattr(
        module, "forge_request", lambda *a, **k: pytest.fail("must not contact the forge"), raising=False
    )

    code = module.main()

    assert code in (0, None)
    assert capsys.readouterr().out.strip().splitlines()[-1] == "landed"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["default", "pr-ci"])
def test_malformed_git_commits_json_names_it_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", "{not valid json")
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.setattr(
        module, "forge_request", lambda *a, **k: pytest.fail("must not contact the forge"), raising=False
    )

    with pytest.raises(SystemExit) as exc:
        module.main()

    assert exc.value.code != 0
    assert "BZ_HUB_GIT_COMMITS" in capsys.readouterr().err


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_an_empty_callback_url_with_a_pending_repo_fails_instead_of_landing_silently(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_double_for(module, calls))

    exit_code = module.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "BZ_HUB_MARKER_CALLBACK_URL" in captured.err


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
    assert land_pr_ci.Verdict([_check_run("completed", conclusion)]).decision == land_pr_ci._FAILED


def test_verdict_waits_on_a_cancelled_conclusion() -> None:
    """A cancellation is re-polled because it may come from a concurrency group."""
    assert land_pr_ci.Verdict([_check_run("completed", "cancelled")]).decision == land_pr_ci._WAIT


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested"])
def test_verdict_waits_for_every_non_terminal_status(status: str) -> None:
    assert land_pr_ci.Verdict([_check_run(status)]).decision == land_pr_ci._WAIT


def test_verdict_waits_on_an_empty_list() -> None:
    assert land_pr_ci.Verdict([]).decision == land_pr_ci._WAIT


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
    assert land_pr_ci.Verdict(check_runs).decision == land_pr_ci._WAIT


def test_verdict_fails_when_any_run_among_several_is_terminal() -> None:
    runs = [_check_run("completed", "success"), _check_run("in_progress"), _check_run("completed", "failure")]
    assert land_pr_ci.Verdict(runs).decision == land_pr_ci._FAILED


def test_findings_names_repo_pr_and_each_failing_check() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 42,
            "url": "https://forge/acme/widget/pull/42",
            "decision": land_pr_ci._FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": False},
            ],
        }
    ]

    text = land_pr_ci.Findings(records).render()

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
            "decision": land_pr_ci._FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": True},
            ],
        }
    ]

    text = land_pr_ci.Findings(records).render()

    assert "not this change" in text


def test_findings_omits_a_base_red_verdict_when_unknown() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 42,
            "url": "https://forge/acme/widget/pull/42",
            "decision": land_pr_ci._FAILED,
            "checks": [
                {"name": "build", "conclusion": "failure", "details_url": "https://forge/build/1", "base_red": None},
            ],
        }
    ]

    text = land_pr_ci.Findings(records).render()

    assert "base branch" not in text


def test_findings_names_the_still_in_flight_checks_for_a_waiting_repo() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 7,
            "url": "https://forge/acme/widget/pull/7",
            "decision": land_pr_ci._WAIT,
            "checks": [{"name": "lint", "status": "in_progress"}, {"name": "test", "status": "queued"}],
        }
    ]

    text = land_pr_ci.Findings(records).render()

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
            "decision": land_pr_ci._FAILED,
            "checks": [{"name": "build", "conclusion": "failure", "details_url": "https://forge/1", "base_red": False}],
        },
        {
            "repo": "acme/gadget",
            "number": 2,
            "url": "https://forge/acme/gadget/pull/2",
            "decision": land_pr_ci._WAIT,
            "checks": [{"name": "test", "status": "queued"}],
        },
    ]

    text = land_pr_ci.Findings(records).render()

    assert "acme/widget" in text
    assert "acme/gadget" in text


# A repo whose declared branch adds nothing to the base branch (the untouched-repo case):
# no PR can be opened, and no poll could change that — it is a no-op landing, not a wait.


def _forge_with_an_empty_repo(
    calls: list[tuple[str, str, dict[str, Any] | None]],
    *,
    other_repo: str,
    other_branch: str,
    compare_status: str = "identical",
    compare_http_status: int = 200,
    base_tip_http_status: int = 200,
):
    """A two-repo double: ``_REPO`` is clean and mergeable, ``other_repo``'s branch is the
    base branch itself, so opening its PR 422s the way the forge refuses an empty PR.
    ``other_repo``'s base branch has moved on to ``basetip`` past the submitted commit."""
    base = f"http://forge/repos/{_REPO}"
    other_base = f"http://forge/repos/{other_repo}"
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": "sha1"}}]),
        ("GET", f"{base}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": False,
                "mergeable_state": "clean",
                "head": {"ref": _BRANCH, "sha": "sha1"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
        ("GET", f"{base}/commits/sha1/check-runs"): (
            200,
            {"total_count": 1, "check_runs": [_check_run("completed", "success")]},
        ),
        ("PUT", f"{base}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
        ("GET", f"{other_base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{other_base}/pulls?state=open"): (200, []),
        ("POST", f"{other_base}/pulls"): (
            422,
            {
                "message": "Validation Failed",
                "errors": [
                    {"resource": "PullRequest", "code": "custom", "message": "No commits between main and main"}
                ],
            },
        ),
        ("GET", f"{other_base}/compare/main...{other_branch}"): (compare_http_status, {"status": compare_status}),
        ("GET", f"{other_base}/git/ref/heads/main"): (base_tip_http_status, {"object": {"sha": "basetip"}}),
    }

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            return 200, {"recorded": True}
        return responses[(method, url)]

    return fake


def _marker_posts(calls: list[tuple[str, str, dict[str, Any] | None]]) -> dict[str, str]:
    return {
        body["name"]: body["content"]
        for m, url, body in calls
        if m == "POST" and url == _CALLBACK_URL and body is not None and body["name"].startswith("merged/")
    }


def _two_repo_env(monkeypatch: pytest.MonkeyPatch, *, other_repo: str, other_branch: str) -> None:
    commits = [
        {"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT},
        {"repo": other_repo, "branch": other_branch, "commit": "basesha"},
    ]
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(commits))


@pytest.mark.parametrize("script", [land_pr_ci, land_default])
def test_a_repo_adding_no_commits_is_a_no_op_landing_and_never_blocks_its_siblings(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_repo, other_branch = "acme/gadget", "main"
    _two_repo_env(monkeypatch, other_repo=other_repo, other_branch=other_branch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common, "forge_request", _forge_with_an_empty_repo(calls, other_repo=other_repo, other_branch=other_branch)
    )

    assert script.main() == 0
    assert _last_line(capsys) == "landed", "an empty repo must not hold the chunk on `pending` forever"

    markers = _marker_posts(calls)
    assert markers[f"merged/{other_repo}"] == "basetip", (
        "a no-op lands at the base's live tip, not the submitted commit"
    )
    assert markers[f"merged/{_REPO}"] == "merged-sha1", "the sibling with real work still merges"


@pytest.mark.parametrize("script", [land_pr_ci, land_default])
def test_a_behind_base_no_op_records_the_base_tip_not_the_submitted_commit(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_repo, other_branch = "acme/gadget", "stale-branch"
    _two_repo_env(monkeypatch, other_repo=other_repo, other_branch=other_branch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_an_empty_repo(calls, other_repo=other_repo, other_branch=other_branch, compare_status="behind"),
    )

    assert script.main() == 0
    assert _last_line(capsys) == "landed"
    assert _marker_posts(calls)[f"merged/{other_repo}"] == "basetip"


@pytest.mark.parametrize(("script", "outcome"), [(land_pr_ci, "pending"), (land_default, "conflict")])
def test_an_unreadable_no_op_base_tip_writes_no_merged_marker(
    script: Any, outcome: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    other_repo, other_branch = "acme/gadget", "stale-branch"
    _two_repo_env(monkeypatch, other_repo=other_repo, other_branch=other_branch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_an_empty_repo(
            calls, other_repo=other_repo, other_branch=other_branch, compare_status="behind", base_tip_http_status=500
        ),
    )

    assert script.main() == 0
    assert _last_line(capsys) == outcome
    assert f"merged/{other_repo}" not in _marker_posts(calls)


def _forge_merging_without_a_sha(
    calls: list[tuple[str, str, dict[str, Any] | None]], *, merge_commit_sha: str | None, merged_before: bool = False
):
    """A one-repo double whose clean PR merges, but whose merge response carries no
    ``sha`` — the reread after the merge is merged, carrying ``merge_commit_sha``."""
    base = f"http://forge/repos/{_REPO}"
    state = {"merged": merged_before}

    def pull() -> dict[str, Any]:
        body: dict[str, Any] = {
            "number": 1,
            "merged": state["merged"],
            "mergeable_state": "clean",
            "head": {"ref": _BRANCH, "sha": "sha1"},
            "html_url": f"http://forge/{_REPO}/pull/1",
        }
        if state["merged"]:
            body["merge_commit_sha"] = merge_commit_sha
        return body

    def fake(
        method: str,
        url: str,
        *,
        token: str | None,
        body: dict[str, Any] | None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        calls.append((method, url, body))
        if url == _CALLBACK_URL:
            return 200, {"recorded": True}
        if url == f"{base}/pulls?state=closed&base=main&page=1&per_page=100":
            merged = [{**pull(), "base": {"ref": "main"}, "merged_at": "t"}] if state["merged"] else []
            return 200, merged
        if url == f"{base}/pulls?state=open":
            return 200, [] if state["merged"] else [{"number": 1, "head": {"ref": _BRANCH, "sha": "sha1"}}]
        if url == f"{base}/pulls/1":
            return 200, pull()
        if url == f"{base}/git/ref/heads/{_BRANCH}":
            return 200, {"object": {"sha": "sha1"}}
        if url == f"{base}/commits/sha1/check-runs":
            return 200, {"total_count": 1, "check_runs": [_check_run("completed", "success")]}
        if method == "PUT" and url == f"{base}/pulls/1/merge":
            state["merged"] = True
            return 200, {"merged": True}
        raise KeyError((method, url))

    return fake


@pytest.mark.parametrize("script", [land_pr_ci, land_default])
def test_a_merge_response_without_a_sha_records_the_rereads_merge_commit(
    script: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_merging_without_a_sha(calls, merge_commit_sha="mc-sha"))

    assert script.main() == 0
    assert _last_line(capsys) == "landed"
    assert _marker_posts(calls) == {f"merged/{_REPO}": "mc-sha"}


@pytest.mark.parametrize(("script", "outcome"), [(land_pr_ci, "pending"), (land_default, "conflict")])
def test_a_merge_whose_landed_commit_no_read_carries_writes_no_merged_marker(
    script: Any, outcome: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_common, "forge_request", _forge_merging_without_a_sha(calls, merge_commit_sha=None))

    assert script.main() == 0
    assert _last_line(capsys) == outcome
    assert _marker_posts(calls) == {}, "never the PR head sha in place of the landed commit"


def test_a_later_land_pr_ci_poll_over_the_now_merged_pr_records_its_merge_commit(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_merging_without_a_sha(calls, merge_commit_sha="mc-sha", merged_before=True),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "landed"
    assert _marker_posts(calls) == {f"merged/{_REPO}": "mc-sha"}


@pytest.mark.parametrize("compare_status, compare_http_status", [("ahead", 200), ("diverged", 200), ("identical", 500)])
def test_a_refusal_that_is_not_an_empty_branch_still_waits_rather_than_landing(
    compare_status: str,
    compare_http_status: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The containment read is what decides, so a real refusal — or an unreadable
    comparison — must never be recorded as a landing."""
    other_repo, other_branch = "acme/gadget", "other-branch"
    _two_repo_env(monkeypatch, other_repo=other_repo, other_branch=other_branch)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_common,
        "forge_request",
        _forge_with_an_empty_repo(
            calls,
            other_repo=other_repo,
            other_branch=other_branch,
            compare_status=compare_status,
            compare_http_status=compare_http_status,
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert f"merged/{other_repo}" not in _marker_posts(calls)
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "chunk atomicity: nothing merges"


def _pull_request_run(fake: Any) -> land_common.LandRun:
    """A minimal :class:`LandRun` for exercising :meth:`PullRequest.of` directly, with no
    markers or commits of its own — the tests below only drive the forge reads."""
    return land_common.LandRun(
        forge_url="http://forge",
        base_branch="main",
        commits=[],
        already=set(),
        markers=land_common.MarkerWriter(
            callback_url="http://hub/markers", token="", request=lambda *args, **kwargs: (200, {})
        ),
        request=fake,
    )


def test_a_stale_merged_pr_on_a_reused_branch_name_is_never_mistaken_for_a_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A branch name is not identity: an earlier, unrelated chunk's merged PR under the
    same branch name must never be read as THIS run's own already-landed attempt (the
    false positive a session-scoped crash-sweep run surfaced)."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": _BRANCH, "sha": "stale-head-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (
            200,
            {"ref": f"refs/heads/{_BRANCH}", "object": {"sha": "current-head-sha", "type": "commit"}},
        ),
        ("GET", f"{base}/pulls?state=open"): (200, []),
        ("POST", f"{base}/pulls"): (201, {"number": 2, "head": {"ref": _BRANCH}}),
        ("GET", f"{base}/pulls/2"): (
            200,
            {"number": 2, "merged": False, "head": {"ref": _BRANCH, "sha": "current-head-sha"}},
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    pr = land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "current-head-sha"})

    assert pr.number == 2, "the stale merged PR (#1) must be ignored — a fresh PR opens instead"
    assert ("POST", f"{base}/pulls") in [(m, u) for m, u, _ in calls]


def test_a_merged_pr_on_the_same_branch_retargeted_to_a_different_base_is_never_mistaken_for_a_landing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A human retarget reuses the branch name against a different base — its merge landed
    somewhere else, so it must never be read as THIS run's own crashed attempt even though
    its head sha still matches the branch's live tip."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, []),
        ("GET", f"{base}/pulls?state=open"): (200, []),
        ("POST", f"{base}/pulls"): (201, {"number": 2, "head": {"ref": _BRANCH}}),
        ("GET", f"{base}/pulls/2"): (
            200,
            {"number": 2, "merged": False, "head": {"ref": _BRANCH, "sha": "current-head-sha"}},
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    pr = land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "current-head-sha"})

    assert pr.number == 2, "a PR merged into a different base must be ignored — a fresh PR opens instead"
    assert ("POST", f"{base}/pulls") in [(m, u) for m, u, _ in calls]


def test_a_merged_candidate_past_the_first_page_of_closed_pulls_is_still_recognized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A full first page of unrelated closed PRs must not stop the scan short — the real
    match, on page two, is still found and recognized without a duplicate PR opening."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    page1 = [
        {
            "number": n,
            "head": {"ref": "other-branch", "sha": f"sha{n}"},
            "base": {"ref": "main"},
            "merged_at": "2024-01-01T00:00:00Z",
        }
        for n in range(1, 101)
    ]
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (200, page1),
        ("GET", f"{base}/pulls?state=closed&base=main&page=2&per_page=100"): (
            200,
            [
                {
                    "number": 101,
                    "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (
            200,
            {"ref": f"refs/heads/{_BRANCH}", "object": {"sha": "the-head-sha", "type": "commit"}},
        ),
        ("GET", f"{base}/pulls/101"): (
            200,
            {
                "number": 101,
                "merged": True,
                "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                "html_url": f"http://forge/{_REPO}/pull/101",
            },
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    pr = land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "the-head-sha"})

    assert pr.number == 101
    assert pr.merged
    assert ("POST", f"{base}/pulls") not in [(m, u) for m, u, _ in calls], "no duplicate PR should open"


def test_an_already_merged_pr_matching_the_live_branch_tip_is_recognized_without_a_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The intended case: a crash after a successful merge but before the marker was
    recorded. Re-entry recognizes the merged PR, whose head sha still matches the
    branch's live tip, and opens no duplicate."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (
            200,
            {"ref": f"refs/heads/{_BRANCH}", "object": {"sha": "the-head-sha", "type": "commit"}},
        ),
        ("GET", f"{base}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": True,
                "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    pr = land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "the-head-sha"})

    assert pr.number == 1
    assert pr.merged
    assert ("POST", f"{base}/pulls") not in [(m, u) for m, u, _ in calls], "no duplicate PR should open"
    assert ("GET", f"{base}/pulls?state=open") not in [(m, u) for m, u, _ in calls], (
        "an already-merged PR is recognized before any open-PR search runs"
    )


def test_an_already_merged_pr_is_recognized_after_the_forge_deleted_its_head_branch() -> None:
    """A crash between the merge and the marker write, on a forge that deletes head
    branches on merge: the live ref is a 404, and the closed PR's own recorded head sha
    still matches the sha the run meant to land."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (404, {"message": "Not Found"}),
        ("GET", f"{base}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": True,
                "head": {"ref": _BRANCH, "sha": "the-head-sha"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    pr = land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "the-head-sha"})

    assert pr.number == 1
    assert pr.merged
    assert ("POST", f"{base}/pulls") not in [(m, u) for m, u, _ in calls]


def test_a_merged_pr_with_another_sha_and_a_deleted_branch_is_not_a_match() -> None:
    """A 404 on the live ref means the branch is gone, not that the read failed: with no
    closed PR matching the sha the run meant to land, there is no merged PR here."""
    base = f"http://forge/repos/{_REPO}"
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": _BRANCH, "sha": "an-older-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (404, {"message": "Not Found"}),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        return responses[(method, url)]

    run = _pull_request_run(fake)
    assert land_common.PullRequest._merged_for_branch(run, _REPO, _BRANCH, "the-head-sha") is None


def test_merged_for_branch_raises_lookup_error_on_a_non_200_closed_pulls_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded closed-pulls read must never be silently read as "nothing merged" — that
    reading is exactly what would reopen a duplicate PR over an already-landed branch."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (503, {"message": "unavailable"}),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    with pytest.raises(land_common.PullRequestLookupError):
        land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "current-head-sha"})


def test_merged_for_branch_raises_lookup_error_on_a_non_200_branch_tip_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same, for the live-tip read that only fires once a merged candidate exists — a
    degraded read there must not silently fall through to "the tip doesn't match"."""
    base = f"http://forge/repos/{_REPO}"
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"{base}/pulls?state=closed&base=main&page=1&per_page=100"): (
            200,
            [
                {
                    "number": 1,
                    "head": {"ref": _BRANCH, "sha": "some-sha"},
                    "base": {"ref": "main"},
                    "merged_at": "2024-01-01T00:00:00Z",
                }
            ],
        ),
        ("GET", f"{base}/git/ref/heads/{_BRANCH}"): (500, {"message": "internal error"}),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    run = _pull_request_run(fake)
    with pytest.raises(land_common.PullRequestLookupError):
        land_common.PullRequest.of(run, {"repo": _REPO, "branch": _BRANCH, "commit": "current-head-sha"})


def test_land_pr_ci_polls_rather_than_reopening_a_duplicate_on_a_degraded_merged_pr_lookup(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The script-level view of the same guarantee: a degraded merged-PR lookup during
    `land_pr_ci` prints `pending`, exactly like the existing create-hiccup case, rather than
    crashing (which would bounce the chunk) or silently opening a duplicate PR."""
    _set_base_env(monkeypatch, feature_title=None)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    responses = {
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=closed&base=main&page=1&per_page=100"): (
            503,
            {"message": "unavailable"},
        ),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None, **_: Any) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    monkeypatch.setattr(land_common, "forge_request", fake)

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not any(m == "POST" for m, _, _ in calls), "no duplicate PR should open on a degraded read"


def test_land_pr_ci_selftest_passes() -> None:
    """Binds `land_pr_ci --selftest`'s pure routing/check/inheritance tables to the unit
    tier — previously reachable only by hand via the CLI flag."""
    assert land_pr_ci._selftest() == 0


def test_a_behind_head_that_is_a_verified_base_merge_updates_on_that_head(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    files = [_file("a.txt", "blob1")]
    outcome, calls = _land_against(
        monkeypatch, capsys, _base_merge(merged_files=files, base_files=files), state="behind"
    )

    assert outcome == "pending"
    update = [body for m, url, body in calls if m == "PUT" and url.endswith("/update-branch")]
    assert update == [{"expected_head_sha": "merge1"}], "update-branch guards on the head the gate verified"
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT"))


# --- the head gate and the base-tip read, driven directly -------------------------------


def _land_run(responses: dict[tuple[str, str], tuple[int, Any]]) -> land_common.LandRun:
    def request(method: str, url: str, *, token: str | None, body: dict[str, Any] | None) -> tuple[int, Any]:
        return responses[(method, url)]

    markers = land_common.MarkerWriter(callback_url=_CALLBACK_URL, token=_MARKER_TOKEN, request=request)
    return land_common.LandRun(
        forge_url="http://forge", base_branch="main", commits=[], already=set(), markers=markers, request=request
    )


def _gate_offenders(responses: dict[tuple[str, str], tuple[int, Any]], head: str = "merge1") -> list[str]:
    return land_pr_ci.gate_head(_land_run(responses), _REPO, "sha1", head).offenders


def _unreadable_compare(base: str, head: str) -> dict[tuple[str, str], tuple[int, Any]]:
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
    with pytest.raises(land_common.ForgeReadDegraded):
        _gate_offenders(responses)


def test_a_file_set_difference_is_a_different_contribution() -> None:
    assert not _matches([_file("a.txt", "b1")], [_file("b.txt", "b1")])


def _chain(commits: list[Any], head: str = "c1") -> tuple[list[dict[str, Any]], str | None]:
    return land_pr_ci._first_parent_chain(commits, head, "sub")


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
    run = _land_run({("GET", f"http://forge/repos/{_REPO}/git/ref/heads/main"): (200, payload)})

    with pytest.raises(land_common.LandedRevisionUnknown):
        run.base_tip(_REPO)


def test_a_base_tip_with_a_sha_is_returned() -> None:
    run = _land_run({("GET", f"http://forge/repos/{_REPO}/git/ref/heads/main"): (200, {"object": {"sha": "tip1"}})})

    assert run.base_tip(_REPO) == "tip1"


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


def _bare_compare(**fields: Any) -> dict[tuple[str, str], tuple[int, Any]]:
    return {("GET", f"http://forge/repos/{_REPO}/compare/sha1...merge1"): (200, fields)}


def test_a_head_equal_to_the_submitted_commit_has_no_offenders() -> None:
    assert land_pr_ci.gate_head(_land_run({}), _REPO, "sha1", "sha1").offenders == []


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
