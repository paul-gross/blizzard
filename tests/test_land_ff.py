"""The fast-forward `deliver` node script — unit tier.

Exercises :func:`~blizzard.hub.graphs.scripts.land_ff.main` in-process against a scripted
``forge_request`` fake (this script's own HTTP seam, ``bzh:deterministic-shell`` — no live
forge, no subprocess, no git). Mirrors ``tests/test_land_scripts.py``'s shape for
``land_default``/``land_pr_ci``, but proves ``land_ff``'s distinct policy: a repo's base
branch ref is advanced directly (``PATCH .../git/refs/heads/<base>``) rather than opened
and merged as a PR — so no ``/pulls`` endpoint is ever hit.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from blizzard.hub.graphs.scripts import land_ff

pytestmark = pytest.mark.unit

_REPO_A = "acme/widget"
_REPO_B = "acme/gadget"
_BRANCH = "feature-branch"
_COMMIT_A = "sha-a"
_COMMIT_B = "sha-b"
_BASE = "main"


def _commits(*repos: str) -> list[dict[str, str]]:
    commit_by_repo = {_REPO_A: _COMMIT_A, _REPO_B: _COMMIT_B}
    return [{"repo": r, "branch": _BRANCH, "commit": commit_by_repo[r]} for r in repos]


def _set_base_env(
    monkeypatch: pytest.MonkeyPatch, *, commits: list[dict[str, str]], already: list[str] | None = None
) -> None:
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge")
    monkeypatch.setenv("BZ_HUB_BASE_BRANCH", _BASE)
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", json.dumps(commits))
    if already is None:
        monkeypatch.delenv("BZ_HUB_ARTIFACT_NAMES", raising=False)
    else:
        monkeypatch.setenv("BZ_HUB_ARTIFACT_NAMES", json.dumps(already))
    monkeypatch.delenv("BZ_FORGE_OWNER", raising=False)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    monkeypatch.delenv("BZ_FORGE_TOKEN", raising=False)


def _last_line(capsys: pytest.CaptureFixture[str]) -> str:
    return capsys.readouterr().out.strip().splitlines()[-1]


def _ref_url(repo: str) -> str:
    return f"http://forge/repos/{repo}/git/ref/heads/{_BASE}"


def _patch_url(repo: str) -> str:
    return f"http://forge/repos/{repo}/git/refs/heads/{_BASE}"


def _scripted_forge(
    calls: list[tuple[str, str, dict[str, Any] | None]],
    *,
    current_shas: dict[str, str],
    patch_status: dict[str, int] | None = None,
    patch_result: dict[str, Any] | None = None,
):
    """A minimal, deterministic double for ``land_ff.forge_request``: a GET on each repo's
    base ref returns the repo's ``current_shas`` entry, a PATCH succeeds (200) unless the
    repo names an override status in ``patch_status``. Records every call for assertion."""
    patch_status = patch_status or {}

    def fake(method: str, url: str, *, token: str | None, body: dict[str, Any] | None) -> tuple[int, Any]:
        calls.append((method, url, body))
        if method == "GET":
            for repo, sha in current_shas.items():
                if url == _ref_url(repo):
                    return 200, {"ref": f"refs/heads/{_BASE}", "object": {"sha": sha, "type": "commit"}}
            raise AssertionError(f"unexpected GET {url}")
        if method == "PATCH":
            for repo in current_shas:
                if url == _patch_url(repo):
                    status = patch_status.get(repo, 200)
                    if status == 200:
                        assert body is not None
                        return 200, {"ref": f"refs/heads/{_BASE}", "object": {"sha": body["sha"]}}
                    return status, patch_result or {"message": "Update is not a fast forward"}
            raise AssertionError(f"unexpected PATCH {url}")
        raise AssertionError(f"unexpected {method} {url}")

    return fake


def test_every_repo_fast_forwards_and_prints_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A, _REPO_B)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a", _REPO_B: "old-b"}),
    )

    assert land_ff.main() == 0
    assert _last_line(capsys) == "landed"

    patches = [(m, u, b) for m, u, b in calls if m == "PATCH"]
    assert len(patches) == 2
    for _, _url, body in patches:
        assert body is not None
        assert body["force"] is False
    assert (
        "PATCH",
        _patch_url(_REPO_A),
        {"sha": _COMMIT_A, "force": False},
    ) in patches
    assert (
        "PATCH",
        _patch_url(_REPO_B),
        {"sha": _COMMIT_B, "force": False},
    ) in patches


def test_non_fast_forward_rejection_prints_conflict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(
            calls,
            current_shas={_REPO_A: "old-a"},
            patch_status={_REPO_A: 422},
        ),
    )

    assert land_ff.main() == 0
    assert _last_line(capsys) == "conflict"


def test_a_repo_with_a_durable_marker_is_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A, _REPO_B)
    _set_base_env(monkeypatch, commits=commits, already=[f"merged/{_REPO_A}"])
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_B: "old-b"}),
    )

    assert land_ff.main() == 0
    assert _last_line(capsys) == "landed"

    urls = [url for _, url, _ in calls]
    assert not any(_REPO_A in url for url in urls), "a repo with a durable marker must be skipped entirely"
    assert _patch_url(_REPO_B) in urls


def test_crash_recovery_treats_an_already_advanced_ref_as_success_not_conflict(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The marker never became durable, but the ref already reads at the target commit —
    # a prior run's update landed and the kill hit before the marker call.
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: _COMMIT_A}),
    )

    assert land_ff.main() == 0
    assert _last_line(capsys) == "landed"

    assert not any(m == "PATCH" for m, _, _ in calls), "an already-at-target ref must not be re-PATCHed"


def test_no_pull_request_endpoint_is_ever_called(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A, _REPO_B)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a", _REPO_B: "old-b"}),
    )

    assert land_ff.main() == 0
    assert _last_line(capsys) == "landed"
    assert not any("/pulls" in url for _, url, _ in calls), "land_ff must never touch a PR endpoint"
