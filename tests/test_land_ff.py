"""The fast-forward `deliver` node script — unit tier.

Exercises :func:`~blizzard.hub.graphs.scripts.land_ff.main` against a scripted
``forge_request`` fake (``bzh:deterministic-shell``). Proves its distinct policy: a
repo's base branch ref is advanced directly rather than opened and merged as a PR.
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

# The mid-run marker callback (issue #230): every advanced repo in these tests records
# a marker, so the scripted forge double needs a response for it too.
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
    monkeypatch.setenv("BZ_HUB_MARKER_CALLBACK_URL", _CALLBACK_URL)
    monkeypatch.setenv("BZ_HUB_MARKER_TOKEN", _MARKER_TOKEN)
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
    marker_headers: list[dict[str, str] | None] | None = None,
    marker_status: int | list[int] = 200,
):
    """A minimal, deterministic double for ``land_ff.forge_request``: a GET on each repo's
    base ref returns the repo's ``current_shas`` entry, a PATCH succeeds (200) unless the
    repo names an override status in ``patch_status``. Records every call for assertion.
    ``marker_status`` scripts the marker POST's response(s) — a single status repeated, or
    a list consumed one response per call (e.g. ``[503, 200]`` for retry-then-succeed)."""
    patch_status = patch_status or {}
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
        if method == "POST" and url == _CALLBACK_URL:
            if marker_headers is not None:
                marker_headers.append(headers)
            status = _next_marker_status(marker_queue, marker_fallback)
            return status, ({} if 200 <= status < 300 else {"message": "marker write failed"})
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


# -- durable marker writes (issue #230) ----------------------------------------------


def test_the_marker_post_carries_the_token_header(monkeypatch: pytest.MonkeyPatch) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    marker_headers: list[dict[str, str] | None] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a"}, marker_headers=marker_headers),
    )

    assert land_ff.main() == 0

    assert marker_headers == [{"X-Blizzard-Marker-Token": _MARKER_TOKEN}]


def test_a_non_2xx_marker_write_aborts_without_printing_landed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a"}, marker_status=401),
    )

    exit_code = land_ff.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "landed" not in captured.err
    assert _REPO_A in captured.err


def test_a_503_then_200_on_the_marker_write_retries_exactly_once_then_lands(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a"}, marker_status=[503, 200]),
    )

    assert land_ff.main() == 0

    marker_calls = [c for c in calls if c[1] == _CALLBACK_URL]
    assert len(marker_calls) == 2  # exactly one retry
    assert _last_line(capsys) == "landed"


def test_an_unset_forge_url_names_it_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    monkeypatch.delenv("BZ_FORGE_URL", raising=False)
    monkeypatch.setattr(
        land_ff, "forge_request", lambda *a, **k: pytest.fail("must not contact the forge"), raising=False
    )

    with pytest.raises(SystemExit) as exc:
        land_ff.main()

    assert exc.value.code != 0
    assert "BZ_FORGE_URL" in capsys.readouterr().err


def test_malformed_git_commits_json_names_it_and_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    monkeypatch.setenv("BZ_HUB_GIT_COMMITS", "{not valid json")
    monkeypatch.setattr(
        land_ff, "forge_request", lambda *a, **k: pytest.fail("must not contact the forge"), raising=False
    )

    with pytest.raises(SystemExit) as exc:
        land_ff.main()

    assert exc.value.code != 0
    assert "BZ_HUB_GIT_COMMITS" in capsys.readouterr().err


def test_an_empty_callback_url_with_a_pending_repo_fails_instead_of_landing_silently(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    commits = _commits(_REPO_A)
    _set_base_env(monkeypatch, commits=commits)
    monkeypatch.delenv("BZ_HUB_MARKER_CALLBACK_URL", raising=False)
    calls: list[tuple[str, str, dict[str, Any] | None]] = []
    monkeypatch.setattr(
        land_ff,
        "forge_request",
        _scripted_forge(calls, current_shas={_REPO_A: "old-a"}),
    )

    exit_code = land_ff.main()

    assert exit_code != 0
    captured = capsys.readouterr()
    assert "landed" not in captured.out
    assert "BZ_HUB_MARKER_CALLBACK_URL" in captured.err
