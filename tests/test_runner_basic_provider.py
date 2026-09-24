"""Real git worktrees in an ordinary folder; no winter CLI or workspace config."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig, WorkspaceRepo
from blizzard.runner.environments.factory import build_workspace_provider
from blizzard.runner.environments.internal.basic_provider import BasicWorkspaceProvider
from blizzard.runner.environments.provider import EnvironmentPreparationError, WorkspaceAcquisitionError
from blizzard.runner.runtime import init_environment


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def origin(tmp_path: Path) -> WorkspaceRepo:
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-b", "main")
    git(seed, "config", "user.name", "Test")
    git(seed, "config", "user.email", "test@example.com")
    (seed / "README.md").write_text("base\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "base")
    git(seed, "remote", "add", "origin", str(bare))
    git(seed, "push", "origin", "main")
    return WorkspaceRepo("toy", str(bare))


@pytest.mark.component
def test_scaffolded_runner_uses_an_ordinary_directory_without_winter(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    runtime = tmp_path / "runner"
    init_environment(runtime)
    init_environment(runtime)  # the unedited scaffold is safe to reconcile
    config_file = runtime / "blizzard-runner.toml"
    config_file.write_text(config_file.read_text() + f'\n[[workspace_repo]]\nname = "toy"\nurl = "{repo.url}"\n')
    config = RunnerConfig.load(runtime)
    provider = build_workspace_provider(config)

    assert config.workspace_provider == "basic"
    assert isinstance(provider, BasicWorkspaceProvider)
    acquired = provider.acquire("01JFULLCHUNK", 1, [])
    assert (Path(acquired[0].workdir) / "toy" / "README.md").read_text() == "base\n"
    assert not (runtime / "workspace" / ".winter").exists()


@pytest.mark.component
def test_acquire_release_and_reacquire_are_clean_and_manifest_scoped(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(str(workspace), repos=(repo,))
    first, second = provider.acquire("01JFULLCHUNK", 2, [])
    assert first.environment_id == "01JFULLCHUNK"
    assert second.environment_id == "01JFULLCHUNK--2"
    assert (workspace / "projects" / "toy").is_dir()
    assert (Path(first.workdir) / "toy" / "README.md").read_text() == "base\n"
    assert provider.repos(first.environment_id)[0].origin_url == repo.url
    assert provider.repos(first.environment_id)[0].relpath == "toy"
    assert provider.repos("unknown") == []

    provider.release(first.environment_id)
    assert provider.repos(first.environment_id) == []
    (Path(first.workdir) / "toy" / "README.md").write_text("dirty\n")
    (Path(first.workdir) / "toy" / "untracked").write_text("dirt")
    # The other environment survives and its path is never reallocated.
    again = BasicWorkspaceProvider(str(workspace), repos=(repo,)).acquire("01JFULLCHUNK", 1, [second.environment_id])
    assert again[0].environment_id == first.environment_id
    assert (Path(first.workdir) / "toy" / "README.md").read_text() == "base\n"
    assert not (Path(first.workdir) / "toy" / "untracked").exists()

    provider.release(second.environment_id)
    survivor = BasicWorkspaceProvider(str(workspace), repos=(repo,)).acquire("01JFULLCHUNK", 1, [first.environment_id])
    assert survivor[0].environment_id == second.environment_id


@pytest.mark.component
def test_reacquire_removes_worktrees_no_longer_configured(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    old_repo = WorkspaceRepo("old", repo.url)
    workspace = tmp_path / "ordinary"
    first = BasicWorkspaceProvider(str(workspace), repos=(repo, old_repo))
    env = first.acquire("chunk", 1, [])[0]
    assert (Path(env.workdir) / "old" / "README.md").exists()
    first.release(env.environment_id)

    current = BasicWorkspaceProvider(str(workspace), repos=(repo,))
    current.acquire("chunk", 1, [])

    assert not (Path(env.workdir) / "old").exists()
    assert [binding.name for binding in current.repos(env.environment_id)] == ["toy"]
    assert (
        "worktree " + str(Path(env.workdir) / "old")
        not in subprocess.run(
            ["git", "-C", str(workspace / "projects" / "old"), "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )


@pytest.mark.component
def test_held_manifest_survives_a_repo_config_change(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    old_repo = WorkspaceRepo("old", repo.url)
    held: list[str] = []
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(str(workspace), repos=(repo, old_repo), held_ids=lambda: held)
    env = provider.acquire("chunk", 1, [])[0]
    held.append(env.environment_id)
    assert not (Path(env.workdir) / ".blizzard-manifests").exists()
    assert (workspace / ".blizzard-manifests" / "chunk.json").is_file()

    restarted = BasicWorkspaceProvider(str(workspace), repos=(repo,), held_ids=lambda: held)
    (Path(env.workdir) / ".blizzard-repos.json").write_text('[{"name": "old", "url": "file:///forged"}]')

    assert {binding.name: binding.origin_url for binding in restarted.repos(env.environment_id)} == {
        "toy": repo.url,
        "old": old_repo.url,
    }
    held.clear()
    assert restarted.repos(env.environment_id) == []


@pytest.mark.component
def test_reacquire_replaces_unregistered_worktree_directory(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(str(workspace), repos=(repo,))
    env = provider.acquire("chunk", 1, [])[0]
    provider.release(env.environment_id)
    worktree = Path(env.workdir) / "toy"
    git(workspace / "projects" / "toy", "worktree", "remove", "--force", str(worktree))
    worktree.mkdir()
    (worktree / "orphan").write_text("stale")

    reacquired = provider.acquire("chunk", 1, [])

    assert reacquired[0].environment_id == env.environment_id
    assert (worktree / "README.md").read_text() == "base\n"
    assert not (worktree / "orphan").exists()


@pytest.mark.component
def test_cap_evicts_only_released_and_refuses_without_partial_allocation(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(str(workspace), repos=(repo,), max_environments=2)
    first, second = provider.acquire("chunk", 2, [])
    with pytest.raises(WorkspaceAcquisitionError, match="cap 2"):
        provider.acquire("other", 1, [first.environment_id, second.environment_id])
    assert not (workspace / "other").exists()
    provider.release(first.environment_id)
    next_env = provider.acquire("other", 1, [second.environment_id])[0]
    assert not Path(first.workdir).exists()
    assert Path(second.workdir).exists()
    assert Path(next_env.workdir).exists()


@pytest.mark.component
def test_manifest_follows_store_fact_across_both_interrupted_release_orders(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    held: list[str] = []
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(str(workspace), repos=(repo,), held_ids=lambda: held)
    env = provider.acquire("chunk", 1, [])[0]
    held.append(env.environment_id)
    provider.release(env.environment_id)  # provider-side release precedes the store write
    assert provider.repos(env.environment_id)
    held.clear()
    assert provider.repos(env.environment_id) == []

    held.append(env.environment_id)
    restarted = BasicWorkspaceProvider(str(workspace), repos=(repo,), held_ids=lambda: held)
    assert restarted.repos(env.environment_id)
    held.clear()  # store-side release precedes provider.release
    assert restarted.repos(env.environment_id) == []


@pytest.mark.component
def test_suffix_cannot_take_another_chunks_full_identifier(tmp_path: Path) -> None:
    repo = origin(tmp_path)
    provider = BasicWorkspaceProvider(str(tmp_path / "ordinary"), repos=(repo,))
    other = provider.acquire("chunk--2", 1, [])[0]
    first, second = provider.acquire("chunk", 2, [other.environment_id])
    assert first.environment_id == "chunk"
    assert second.environment_id == "chunk--3"
    assert (Path(other.workdir) / "toy" / "README.md").read_text() == "base\n"


@pytest.mark.component
def test_failed_clone_does_not_leave_a_partial_environment(tmp_path: Path) -> None:
    workspace = tmp_path / "ordinary"
    provider = BasicWorkspaceProvider(
        str(workspace), repos=(origin(tmp_path), WorkspaceRepo("missing", str(tmp_path / "missing.git")))
    )
    with pytest.raises(EnvironmentPreparationError) as caught:
        provider.acquire("chunk", 2, [])
    assert caught.value.step == "git-worktree"
    assert not (workspace / "chunk").exists()
    assert not (workspace / "chunk--2").exists()
