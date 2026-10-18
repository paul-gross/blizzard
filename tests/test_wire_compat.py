"""blizzard:wire-compat (bzh:fleet-wire-additive) — the git-history walk and baseline resolution, on a
real throwaway repo, per ``tests/test_runner_basic_provider.py``."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blizzard.tools import wire_compat
from blizzard.tools.compat.gate import BREAKING_COMMIT_MARKER
from blizzard.tools.wire_compat import WireCompatError, check_history

pytestmark = pytest.mark.unit


# --- fixture-spec helpers -----------------------------------------------------------


def _spec(paths: dict, schemas: dict | None = None) -> dict:
    return {"openapi": "3.1.0", "paths": paths, "components": {"schemas": schemas or {}}}


def _ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def _op(*, response_schema: dict | None = None, request_schema: dict | None = None) -> dict:
    op: dict = {"responses": {}}
    if response_schema is not None:
        op["responses"]["200"] = {"content": {"application/json": {"schema": response_schema}}}
    else:
        op["responses"]["204"] = {"description": "no content"}
    if request_schema is not None:
        op["requestBody"] = {"content": {"application/json": {"schema": request_schema}}}
    return op


def _violation_texts(violations) -> list[str]:
    return [str(v) for v in violations]


# --- git history walk + ! acknowledgement -----------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def _commit_spec(repo: Path, spec: dict, message: str) -> str:
    spec_dir = repo / "openapi"
    spec_dir.mkdir(exist_ok=True)
    (spec_dir / "hub.openapi.json").write_text(json.dumps(spec))
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def test_check_history_fails_on_unacknowledged_break(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat: drop the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is False


def test_check_history_passes_on_acknowledged_break(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat!: drop the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_scoped_marker_also_acknowledges() -> None:
    import re

    assert BREAKING_COMMIT_MARKER.match("feat(runner)!: drop a route")
    assert not re.match(BREAKING_COMMIT_MARKER.pattern, "feat(runner): add a route")


def test_check_history_one_acknowledged_break_never_masks_another(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(
        repo, _spec({"/api/fleet/a": {"get": _op()}, "/api/fleet/b": {"get": _op()}}), "chore: base"
    )
    _commit_spec(repo, _spec({"/api/fleet/b": {"get": _op()}}), "feat!: drop a")
    _commit_spec(repo, _spec({}), "feat: drop b")
    assert check_history(base_commit, repo, echo=lambda *_: None) is False


def test_check_history_is_a_noop_with_no_changes_since_baseline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_passes_when_the_step_is_additive(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(
        repo, _spec({"/api/fleet/widgets": {"get": _op()}, "/api/fleet/gadgets": {"get": _op()}}), "feat: add gadgets"
    )
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_recovers_once_an_unacknowledged_break_is_reverted(tmp_path: Path) -> None:
    # An unacknowledged break landing on master can't be marked `!` after the fact and pushed
    # history can't be rewritten — reverting it makes the net diff additive again.
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _commit_spec(repo, _spec({}), "feat: drop the widgets route")
    _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "feat: restore the widgets route")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


# --- deployed-mode baseline resolution --------------------------------------------------


class _FakeCompleted:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _gh_stub(listing: list[dict], rest: list[dict] | dict | None = None, calls: list | None = None):
    """A stubbed ``gh``: ``run list`` answers ``listing``, ``api`` answers the REST payload
    (defaulting to the same runs in REST field names)."""
    if rest is None:
        rest = [
            {"head_sha": r["headSha"], "conclusion": r["conclusion"], "created_at": r["createdAt"]} for r in listing
        ]
    payload = rest if isinstance(rest, dict) else {"workflow_runs": rest}

    def fake_run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if cmd[:3] == ["gh", "run", "list"]:
            return _FakeCompleted(stdout=json.dumps(listing))
        assert cmd[:2] == ["gh", "api"]
        return _FakeCompleted(stdout=json.dumps(payload))

    return fake_run


def _run(sha: str, conclusion: str, created: str) -> dict:
    return {"headSha": sha, "conclusion": conclusion, "createdAt": created}


def test_resolve_deployed_baseline_reads_gh_run_list(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list = []
    stub = _gh_stub([_run("deadbeef", "success", "2026-10-05T10:00:00Z")], calls=calls)
    monkeypatch.setattr(wire_compat.subprocess, "run", stub)
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path) == "deadbeef"
    assert calls[0][:3] == ["gh", "run", "list"]
    assert "push.yml" in calls[0]


def test_resolve_deployed_baseline_skips_unsuccessful_runs_without_a_status_filter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """GitHub answers ``--branch`` with ``--status`` from a stale index, so success is picked
    from the newest runs here: an in-progress and a failed run are passed over."""
    calls: list = []
    runs = [
        _run("inflight", "", "2026-10-05T13:00:00Z"),
        _run("broken", "failure", "2026-10-05T12:00:00Z"),
        _run("deployed", "success", "2026-10-05T11:00:00Z"),
        _run("older", "success", "2026-10-05T10:00:00Z"),
    ]
    monkeypatch.setattr(wire_compat.subprocess, "run", _gh_stub(runs, calls=calls))
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path) == "deployed"
    assert all("--status" not in c for c in calls)


def test_resolve_deployed_baseline_picks_newest_success_by_creation_time(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runs = [_run("older", "success", "2026-10-04T10:00:00Z"), _run("newest", "success", "2026-10-05T10:00:00Z")]
    monkeypatch.setattr(wire_compat.subprocess, "run", _gh_stub(runs))
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path) == "newest"


def test_resolve_deployed_baseline_retries_a_stale_listing_until_it_agrees(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stale = [_run("september", "success", "2026-09-08T10:00:00Z")]
    fresh = [_run("current", "success", "2026-10-05T10:00:00Z")]
    rest = [{"head_sha": "current", "conclusion": "success", "created_at": "2026-10-05T10:00:00Z"}]
    answers = iter([stale, fresh])
    current = {}

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["gh", "run", "list"]:
            current["listing"] = next(answers)
            return _FakeCompleted(stdout=json.dumps(current["listing"]))
        return _FakeCompleted(stdout=json.dumps({"workflow_runs": rest}))

    sleeps: list[float] = []
    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    assert wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path, sleep=sleeps.append) == "current"
    assert len(sleeps) == 1


def test_resolve_deployed_baseline_fails_naming_a_listing_that_stays_stale(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stale = [_run("september", "success", "2026-09-08T10:00:00Z")]
    rest = [{"head_sha": "current", "conclusion": "success", "created_at": "2026-10-05T10:00:00Z"}]
    sleeps: list[float] = []
    monkeypatch.setattr(wire_compat.subprocess, "run", _gh_stub(stale, rest))
    with pytest.raises(WireCompatError, match=r"stale push\.yml run listing"):
        wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path, sleep=sleeps.append)
    assert len(sleeps) == wire_compat._DEPLOYED_ATTEMPTS - 1


def test_resolve_deployed_baseline_fails_when_no_successful_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(wire_compat.subprocess, "run", _gh_stub([_run("broken", "failure", "2026-10-05T10:00:00Z")]))
    with pytest.raises(WireCompatError):
        wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path)


def test_resolve_deployed_baseline_fails_when_gh_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_run(cmd, **kwargs):
        return _FakeCompleted(returncode=1, stderr="not authenticated")

    monkeypatch.setattr(wire_compat.subprocess, "run", fake_run)
    with pytest.raises(WireCompatError):
        wire_compat.resolve_deployed_baseline("paul-gross/blizzard", tmp_path)


def test_resolve_merge_base_baseline(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({}), "chore: base")
    _git(repo, "branch", "other")
    _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "feat: add widgets")
    assert wire_compat.resolve_merge_base_baseline("other", repo) == base_commit


# --- acknowledgement across a merge landing ---------------------------------------------


def test_check_history_acknowledges_a_break_landed_by_a_merge_of_a_marked_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _git(repo, "checkout", "-b", "side")
    _commit_spec(repo, _spec({}), "feat!: drop the widgets route")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "side", "-m", "Merge pull request #1 from side")
    assert check_history(base_commit, repo, echo=lambda *_: None) is True


def test_check_history_fails_a_merge_landing_with_no_marked_commit(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path)
    base_commit = _commit_spec(repo, _spec({"/api/fleet/widgets": {"get": _op()}}), "chore: base")
    _git(repo, "checkout", "-b", "side")
    _commit_spec(repo, _spec({}), "feat: drop the widgets route")
    _git(repo, "checkout", "main")
    _git(repo, "merge", "--no-ff", "side", "-m", "Merge pull request #1 from side")
    assert check_history(base_commit, repo, echo=lambda *_: None) is False
