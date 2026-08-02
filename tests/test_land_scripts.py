"""The default/PR-CI land scripts' PR title and merge commit message — unit tier.

Exercises :func:`~blizzard.hub.graphs.scripts.land_default.main` and
:func:`~blizzard.hub.graphs.scripts.land_pr_ci.main` in-process against a scripted
``forge_request`` fake (each script's own HTTP seam, ``bzh:deterministic-shell`` — no
live forge, no subprocess): the one behavior this module owns is that the opened PR's
``title`` is JUST the hub-resolved ``BZ_HUB_FEATURE_TITLE`` (truncated to GitHub's
256-char cap), falling back to the bare branch name — never a ``blizzard: land``
prefix — while the merge's ``commit_message`` prefers the title and falls back to the
``blizzard: land ...`` string when it is absent.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blizzard.hub.graphs.scripts import land_default, land_ff, land_pr_ci

pytestmark = pytest.mark.unit

_REPO = "acme/widget"
_BRANCH = "feature-branch"
_COMMIT = "sha1"
_COMMITS = [{"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT}]

# The mid-run marker callback (issue #230): every pushed/merged repo in these tests
# records a marker, so every scripted forge double needs a response for it too.
_CALLBACK_URL = "http://callback/hub-markers"
_MARKER_TOKEN = "test-marker-token"


def _marker_status_queue(marker_status: int | list[int]) -> tuple[list[int] | None, int]:
    """Normalize ``marker_status`` into a (queue, fallback) pair: a list is consumed one
    response per call, a bare int repeats forever."""
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
    """A minimal, deterministic double for ``land_default.forge_request`` — one repo,
    no existing PR, a clean merge. Records every call for assertion. ``marker_status``
    lets a caller script the marker POST's response(s) (a single status repeated, or a
    list consumed one response per call — e.g. ``[503, 200]`` for a retry-then-succeed
    scenario)."""
    responses = {
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=open"): (200, []),
        ("POST", f"http://forge/repos/{_REPO}/pulls"): (201, {"number": 1, "head": {"ref": _BRANCH}}),
        ("GET", f"http://forge/repos/{_REPO}/pulls/1"): (
            200,
            {"number": 1, "merged": False, "mergeable_state": "clean"},
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
    monkeypatch.setattr(module, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    assert _pr_title(calls) == "Add rate limiting to the widget API"
    assert _merge_commit_message(calls) == "Add rate limiting to the widget API"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_missing_feature_title_falls_back_to_the_branch_and_land_strings(
    monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    _set_base_env(monkeypatch, feature_title=None)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(module, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    # the PR title is the bare branch — no `blizzard: land` prefix ...
    assert _pr_title(calls) == _BRANCH
    # ... but the merge commit body keeps the `blizzard: land <repo>` fallback.
    assert _merge_commit_message(calls) == f"blizzard: land {_REPO}"


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_an_over_long_feature_title_is_truncated_for_the_pr_title(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    long_title = "x" * 300
    _set_base_env(monkeypatch, feature_title=long_title)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(module, "forge_request", _scripted_forge(calls))

    assert module.main() == 0

    title = _pr_title(calls)
    assert len(title) == 256  # GitHub's cap: 255 chars + the ellipsis
    assert title.endswith("…")
    # the merge commit message is a commit body, not a PR title — left untruncated.
    assert _merge_commit_message(calls) == long_title


# -- land_pr_ci self-heal routing (component tier) --------------------------------
#
# The mergeable-state state machine: `land_pr_ci` opens a PR per repo and routes by its
# live `mergeable_state` — heal `behind`, wait out transient/CI-not-green, bounce only a
# real `dirty`. The pure decision is `classify()` (`--selftest`); these assert `main()`'s
# actual forge calls + printed outcome against a scripted double for one existing PR.


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
):
    """A double whose one already-open PR reads ``mergeable_state``. Records every call.

    ``head_check_runs``/``base_check_runs`` (issue #232), when given, stub the
    ``GET .../commits/{ref}/check-runs`` routes for the PR's head sha and the base
    branch (``"main"``) respectively — a route left unstubbed (the default) raises
    ``KeyError`` on lookup, exactly like every other unstubbed route in this double,
    so the degradation path under test reacts to the same real failure mode
    ``forge_request`` surfaces from a genuine outage, not a test-double artifact.
    """
    base = f"http://forge/repos/{_REPO}"
    pull = {
        "number": 1,
        "merged": merged,
        "mergeable_state": mergeable_state,
        "head": {"ref": _BRANCH, "sha": "headsha"},
        "html_url": f"http://forge/{_REPO}/pull/1",
    }
    responses = {
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": "headsha"}}]),
        ("GET", f"{base}/pulls/1"): (200, pull),
        ("PUT", f"{base}/pulls/1/update-branch"): (update_status, {"message": "Updating pull request branch."}),
        ("PUT", f"{base}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
    }
    if head_check_runs is not None:
        responses[("GET", f"{base}/commits/headsha/check-runs")] = (
            head_check_runs_status,
            {"total_count": len(head_check_runs), "check_runs": head_check_runs},
        )
    if base_check_runs is not None:
        responses[("GET", f"{base}/commits/main/check-runs")] = (
            base_check_runs_status,
            {"total_count": len(base_check_runs), "check_runs": base_check_runs},
        )
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
    monkeypatch.setattr(land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="dirty"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "conflict"  # the ONE true bounce
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "a dirty PR must not be merged"
    assert not any(url.endswith("/update-branch") for url in _urls(calls, "PUT"))


def test_behind_pr_fires_update_branch_and_pends(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="behind"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # self-heal in flight, re-poll
    update = [body for m, url, body in calls if m == "PUT" and url.endswith("/update-branch")]
    assert update, "a behind PR must request update-branch"
    assert update[0] == {"expected_head_sha": "headsha"}, "update-branch must guard on the current head"
    assert not any(url.endswith("/merge") for url in _urls(calls, "PUT")), "nothing merges while behind"


def test_blocked_pr_pends_without_updating_or_merging(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="blocked"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"  # required checks not green — wait, do not bounce
    assert not any(url.endswith(("/merge", "/update-branch")) for url in _urls(calls, "PUT"))


def test_clean_pr_merges_the_current_head_sha(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="clean"))

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "landed"
    merge = [body for m, url, body in calls if m == "PUT" and url.endswith("/merge") and body is not None]
    assert merge and merge[0]["sha"] == "headsha", "a self-heal must merge the CURRENT head, not a stale commit"


# -- land_pr_ci terminal CI check failure + CI-watch findings (issue #232) ----------
#
# `classify`'s `blocked`/`unstable` wait is the CI-watch case; these assert `main()`'s
# actual check-runs GETs, the `delivery-findings` marker write, and the graph's authored
# `failure` edge (`advanced-development-workflow/graph.yaml`'s `deliver` node authors
# exactly `landed`/`failure`) against a scripted double.


def _check_runs_urls(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[str]:
    return [url for m, url, _ in calls if m == "GET" and url.endswith("/check-runs")]


def _findings_posts(calls: list[tuple[str, str, dict[str, Any] | None]]) -> list[dict[str, Any]]:
    return [body for m, url, body in calls if m == "POST" and url == _CALLBACK_URL and body is not None]


@pytest.mark.parametrize("state", ["blocked", "unstable"])
def test_a_terminal_check_failure_prints_the_failure_edge_and_writes_findings(
    state: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_pr_ci,
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


def test_a_base_branch_also_red_names_the_change_as_not_at_fault(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_pr_ci,
        "forge_request",
        _forge_with_state(
            calls,
            mergeable_state="blocked",
            head_check_runs=[_check_run("completed", "failure")],
            base_check_runs=[_check_run("completed", "failure")],
        ),
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "failure"

    posts = _findings_posts(calls)
    assert len(posts) == 1
    assert "not this change" in posts[0]["content"]


def test_a_check_runs_read_failure_degrades_to_a_plain_pending_not_the_failure_edge(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    _set_base_env(monkeypatch, feature_title="t")
    monkeypatch.setattr(
        land_pr_ci,
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
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=open"): (
            200,
            [{"number": 1, "head": {"ref": _BRANCH, "sha": "headsha"}}],
        ),
        ("GET", f"http://forge/repos/{_REPO}/pulls/1"): (
            200,
            {
                "number": 1,
                "merged": False,
                "mergeable_state": "blocked",
                "head": {"ref": _BRANCH, "sha": "headsha"},
                "html_url": f"http://forge/{_REPO}/pull/1",
            },
        ),
        ("GET", f"http://forge/repos/{_REPO}/commits/headsha/check-runs"): (
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

    monkeypatch.setattr(land_pr_ci, "forge_request", fake)

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
        land_pr_ci,
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
    monkeypatch.setattr(land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="unknown"))

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
        land_pr_ci, "forge_request", _forge_with_state(calls, mergeable_state="blocked", head_check_runs=[])
    )

    assert land_pr_ci.main() == 0
    assert _last_line(capsys) == "pending"
    assert not _findings_posts(calls)


@pytest.mark.parametrize("script", [land_default, land_pr_ci, land_ff], ids=["default", "pr-ci", "ff"])
def test_an_empty_commit_set_fails_the_node_instead_of_reporting_landed(
    script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression that let a fully-built feature reach `done` undelivered.

    Every land policy filtered `commits` through the `merged/<repo>` markers and printed
    `landed` when nothing remained — which is right when the markers are all present, and
    catastrophically wrong when there were no commits to begin with. The two cases are
    indistinguishable after the filter, so the empty *input* is caught before it.
    """
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


@pytest.mark.parametrize("script", [land_default, land_pr_ci, land_ff], ids=["default", "pr-ci", "ff"])
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


@pytest.mark.parametrize("script", [land_default, land_pr_ci, land_ff], ids=["default", "pr-ci", "ff"])
def test_a_non_code_chunk_lands_empty_because_its_graph_promised_no_commit(
    script, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """MVP criterion 10: a review or a spike produces only assets, declares no
    `git_commit` anywhere in its graph, and still routes through `deliver` as the uniform
    terminal. Landing nothing is its correct outcome, not a defect — emptiness alone
    cannot tell the two apart, which is exactly why the graph's intent is injected."""
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


@pytest.mark.parametrize("script", [land_default, land_pr_ci, land_ff], ids=["default", "pr-ci", "ff"])
def test_an_absent_expectation_signal_is_treated_as_expected(script, monkeypatch: pytest.MonkeyPatch) -> None:
    """An older executor injects no signal. Of the two possible defaults, failing loudly
    on a set the policy cannot explain is the safer one — the alternative silently
    restores the exact behavior that let a built feature reach `done` undelivered."""
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


# -- durable marker writes (issue #230) ----------------------------------------------
#
# `land_default` and `land_pr_ci` share the same PR-open-then-merge shape, so their
# marker-write behavior is exercised together here via `_scripted_forge`/
# `_forge_with_state`; `land_ff`'s own fixture shape lives in ``tests/test_land_ff.py``.


def _forge_double_for(module: Any, calls: list[tuple[str, str, dict[str, Any] | None]], **kwargs: Any):
    """The right scripted double for ``module`` — both share ``_REPO``/``_BRANCH``, but
    ``land_pr_ci`` reads a live ``mergeable_state`` where ``land_default`` reads a fresh
    PR, so each needs its own fixture shape."""
    if module is land_pr_ci:
        return _forge_with_state(calls, mergeable_state="clean", **kwargs)
    return _scripted_forge(calls, **kwargs)


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_the_marker_post_carries_the_token_header(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    marker_headers: list[dict[str, str] | None] = []
    monkeypatch.setattr(module, "forge_request", _forge_double_for(module, calls, marker_headers=marker_headers))

    assert module.main() == 0

    assert marker_headers == [{"X-Blizzard-Marker-Token": _MARKER_TOKEN}]


@pytest.mark.parametrize("module", [land_default, land_pr_ci], ids=["land_default", "land_pr_ci"])
def test_a_non_2xx_marker_write_aborts_without_printing_landed(
    monkeypatch: pytest.MonkeyPatch, module: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    _set_base_env(monkeypatch, feature_title="t")
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(module, "forge_request", _forge_double_for(module, calls, marker_status=401))

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
    monkeypatch.setattr(module, "forge_request", _forge_double_for(module, calls, marker_status=[503, 200]))

    assert module.main() == 0

    marker_calls = [c for c in calls if c[1] == _CALLBACK_URL]
    assert len(marker_calls) == 2  # exactly one retry
    assert capsys.readouterr().out.strip().splitlines()[-1] == "landed"


@pytest.mark.parametrize("module", [land_default, land_ff, land_pr_ci], ids=["default", "ff", "pr-ci"])
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


@pytest.mark.parametrize("module", [land_default, land_ff, land_pr_ci], ids=["default", "ff", "pr-ci"])
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
    monkeypatch.setattr(module, "forge_request", _forge_double_for(module, calls))

    exit_code = module.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "BZ_HUB_MARKER_CALLBACK_URL" in captured.err


# -- land_pr_ci.classify_checks + render_findings (issue #232) ----------------------
#
# A terminally-failed check run must never be polled out to `poll_timeout` — these are
# the pure, network-free functions a later phase wires into `main()`'s check stage and
# a `delivery-findings` marker artifact.


def _check_run(status: str, conclusion: str | None = None) -> dict[str, Any]:
    return {
        "id": 1,
        "name": "build",
        "status": status,
        "conclusion": conclusion,
        "details_url": "https://forge/build/1",
        "head_sha": "headsha",
    }


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "cancelled", "action_required"])
def test_classify_checks_is_failed_for_every_terminal_conclusion(conclusion: str) -> None:
    assert land_pr_ci.classify_checks([_check_run("completed", conclusion)]) == land_pr_ci._FAILED


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested"])
def test_classify_checks_waits_for_every_non_terminal_status(status: str) -> None:
    assert land_pr_ci.classify_checks([_check_run(status)]) == land_pr_ci._WAIT


def test_classify_checks_waits_on_an_empty_list() -> None:
    assert land_pr_ci.classify_checks([]) == land_pr_ci._WAIT


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
def test_classify_checks_degrades_to_wait_on_a_malformed_payload_without_raising(check_runs: Any) -> None:
    assert land_pr_ci.classify_checks(check_runs) == land_pr_ci._WAIT


def test_classify_checks_fails_when_any_run_among_several_is_terminal() -> None:
    runs = [_check_run("completed", "success"), _check_run("in_progress"), _check_run("completed", "failure")]
    assert land_pr_ci.classify_checks(runs) == land_pr_ci._FAILED


def test_render_findings_names_repo_pr_and_each_failing_check() -> None:
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

    text = land_pr_ci.render_findings(records)

    assert _REPO in text
    assert "42" in text
    assert "https://forge/acme/widget/pull/42" in text
    assert "build" in text
    assert "failure" in text
    assert "https://forge/build/1" in text


def test_render_findings_names_a_broken_base_as_not_this_change() -> None:
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

    text = land_pr_ci.render_findings(records)

    assert "not this change" in text


def test_render_findings_omits_a_base_red_verdict_when_unknown() -> None:
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

    text = land_pr_ci.render_findings(records)

    assert "base branch" not in text


def test_render_findings_names_the_still_in_flight_checks_for_a_waiting_repo() -> None:
    records = [
        {
            "repo": _REPO,
            "number": 7,
            "url": "https://forge/acme/widget/pull/7",
            "decision": land_pr_ci._WAIT,
            "checks": [{"name": "lint", "status": "in_progress"}, {"name": "test", "status": "queued"}],
        }
    ]

    text = land_pr_ci.render_findings(records)

    assert _REPO in text
    assert "7" in text
    assert "lint" in text
    assert "in_progress" in text
    assert "test" in text
    assert "queued" in text
    # a wait record carries no conclusion/details_url at all — only name + status.
    assert "conclusion" not in text.lower()


def test_render_findings_joins_multiple_repos() -> None:
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

    text = land_pr_ci.render_findings(records)

    assert "acme/widget" in text
    assert "acme/gadget" in text
