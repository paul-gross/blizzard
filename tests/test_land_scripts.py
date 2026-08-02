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
):
    """A double whose one already-open PR reads ``mergeable_state``. Records every call."""
    base = f"http://forge/repos/{_REPO}"
    pull = {
        "number": 1,
        "merged": merged,
        "mergeable_state": mergeable_state,
        "head": {"ref": _BRANCH, "sha": "headsha"},
    }
    responses = {
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": "headsha"}}]),
        ("GET", f"{base}/pulls/1"): (200, pull),
        ("PUT", f"{base}/pulls/1/update-branch"): (update_status, {"message": "Updating pull request branch."}),
        ("PUT", f"{base}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
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
