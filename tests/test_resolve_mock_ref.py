from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / ".github" / "actions" / "resolve-mock-ref" / "resolve-mock-ref.sh"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.test",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.test",
}


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env={**os.environ, **_GIT_ENV}
    )
    return result.stdout.strip()


@pytest.fixture(scope="module")
def mock(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    root = tmp_path_factory.mktemp("mock")
    work = root / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "master")
    shas: dict[str, str] = {}
    for name in ("master", "release/0.2", "feat/x"):
        _git(work, "checkout", "-q", "-B", name, *(["master"] if name != "master" else []))
        _git(work, "commit", "-q", "--allow-empty", "-m", name)
        shas[name] = _git(work, "rev-parse", "HEAD")
    _git(work, "tag", "-a", "v0.2.0", shas["release/0.2"], "-m", "v0.2.0")
    shas["tag-object"] = _git(work, "rev-parse", "v0.2.0")
    bare = root / "mock.git"
    _git(root, "clone", "-q", "--bare", str(work), str(bare))
    shas["url"] = bare.as_uri()
    return shas


def _resolve(mock: dict[str, str], **context: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"BASELINE", "EVENT_NAME", "REF", "BASE_REF", "HEAD_REF"}}
    env.update(MOCK_URL=mock["url"], **context)
    return subprocess.run([str(_SCRIPT)], capture_output=True, text=True, env=env)


def _outputs(result: subprocess.CompletedProcess[str]) -> dict[str, str]:
    assert result.returncode == 0, result.stderr
    return dict(line.split("=", 1) for line in result.stdout.splitlines())


def test_a_tag_run_resolves_the_tag_to_the_commit_it_peels_to(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="push", REF="refs/tags/v0.2.0"))
    assert out == {"ref": "v0.2.0", "sha": mock["release/0.2"]}
    assert out["sha"] != mock["tag-object"]


def test_a_tag_run_fails_naming_the_missing_tag(mock) -> None:
    result = _resolve(mock, EVENT_NAME="push", REF="refs/tags/v9.9.9")
    assert result.returncode != 0
    assert "::error::" in result.stderr and "v9.9.9" in result.stderr


def test_a_release_pull_request_uses_the_matching_mock_branch(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="pull_request", BASE_REF="release/0.2", HEAD_REF="feat/x"))
    assert out == {"ref": "feat/x", "sha": mock["feat/x"]}


def test_a_release_pull_request_falls_back_to_the_release_branch(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="pull_request", BASE_REF="release/0.2", HEAD_REF="fix/other"))
    assert out == {"ref": "release/0.2", "sha": mock["release/0.2"]}


def test_a_release_pull_request_fails_naming_the_missing_release_branch(mock) -> None:
    result = _resolve(mock, EVENT_NAME="pull_request", BASE_REF="release/9.9", HEAD_REF="fix/other")
    assert result.returncode != 0
    assert "::error::" in result.stderr and "release/9.9" in result.stderr


def test_a_master_pull_request_uses_the_matching_mock_branch(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="pull_request", BASE_REF="master", HEAD_REF="feat/x"))
    assert out == {"ref": "feat/x", "sha": mock["feat/x"]}


def test_a_master_pull_request_falls_back_to_master(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="pull_request", BASE_REF="master", HEAD_REF="fix/other"))
    assert out == {"ref": "master", "sha": mock["master"]}


def test_a_push_to_master_resolves_master(mock) -> None:
    out = _outputs(_resolve(mock, EVENT_NAME="push", REF="refs/heads/master"))
    assert out == {"ref": "master", "sha": mock["master"]}


def test_a_baseline_resolves_to_the_mock_tag_of_that_name(mock) -> None:
    out = _outputs(_resolve(mock, BASELINE="v0.2.0", EVENT_NAME="push", REF="refs/heads/master"))
    assert out == {"ref": "v0.2.0", "sha": mock["release/0.2"]}


def test_a_baseline_the_mock_never_tagged_falls_back_to_master(mock) -> None:
    out = _outputs(_resolve(mock, BASELINE="v0.1.0", EVENT_NAME="push", REF="refs/tags/v9.9.9"))
    assert out == {"ref": "master", "sha": mock["master"]}


@pytest.mark.parametrize(
    "context",
    [
        {"EVENT_NAME": "push", "REF": "refs/heads/release/0.2"},
        {"EVENT_NAME": "pull_request", "BASE_REF": "develop", "HEAD_REF": "feat/x"},
        {"EVENT_NAME": "workflow_dispatch", "REF": "refs/heads/master"},
    ],
)
def test_a_context_matching_no_row_fails_rather_than_defaulting_to_master(mock, context) -> None:
    result = _resolve(mock, **context)
    assert result.returncode != 0
    assert "::error::" in result.stderr
    assert result.stdout == ""
