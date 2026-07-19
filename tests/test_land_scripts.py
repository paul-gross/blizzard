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

from blizzard.hub.graphs.scripts import land_default, land_pr_ci

pytestmark = pytest.mark.unit

_REPO = "acme/widget"
_BRANCH = "feature-branch"
_COMMIT = "sha1"
_COMMITS = [{"repo": _REPO, "branch": _BRANCH, "commit": _COMMIT}]


def _scripted_forge(calls: list[tuple[str, str, dict[str, Any] | None]]):
    """A minimal, deterministic double for ``land_default.forge_request`` — one repo,
    no existing PR, a clean merge. Records every call for assertion."""
    responses = {
        ("GET", f"http://forge/repos/{_REPO}/pulls?state=open"): (200, []),
        ("POST", f"http://forge/repos/{_REPO}/pulls"): (201, {"number": 1, "head": {"ref": _BRANCH}}),
        ("GET", f"http://forge/repos/{_REPO}/pulls/1"): (
            200,
            {"number": 1, "merged": False, "mergeable_state": "clean"},
        ),
        ("PUT", f"http://forge/repos/{_REPO}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None) -> tuple[int, Any]:
        calls.append((method, url, body))
        return responses[(method, url)]

    return fake


def _set_base_env(monkeypatch: pytest.MonkeyPatch, *, feature_title: str | None) -> None:
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", "main")
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(_COMMITS))
    monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    monkeypatch.delenv("BZ_FORGE_OWNER", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
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
):
    """A double whose one already-open PR reads ``mergeable_state``. Records every call."""
    base = f"http://forge/repos/{_REPO}"
    pull = {"number": 1, "merged": merged, "mergeable_state": mergeable_state, "head": {"ref": _BRANCH, "sha": "headsha"}}
    responses = {
        ("GET", f"{base}/pulls?state=open"): (200, [{"number": 1, "head": {"ref": _BRANCH, "sha": "headsha"}}]),
        ("GET", f"{base}/pulls/1"): (200, pull),
        ("PUT", f"{base}/pulls/1/update-branch"): (update_status, {"message": "Updating pull request branch."}),
        ("PUT", f"{base}/pulls/1/merge"): (200, {"sha": "merged-sha1", "merged": True}),
    }

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None) -> tuple[int, Any]:
        calls.append((method, url, body))
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
    merge = [body for m, url, body in calls if m == "PUT" and url.endswith("/merge")]
    assert merge and merge[0]["sha"] == "headsha", "a self-heal must merge the CURRENT head, not a stale commit"
