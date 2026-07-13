"""The winter workspace provider — allocation logic (unit) and the real CLI drive (component).

The allocation contract (D-062) is unit-tested with fake winter/git sub-seams: pick
from the pool minus the held set, all-or-nothing refusal, reset-on-acquire ordering,
and no-op release. The component test drives the **real** ``winter`` CLI + git
against a minimal real workspace over a ``file://`` origin — acquire creates the
feature env and returns a clean worktree, and a dirty file is erased on the next
acquire (reset-on-acquire, D-021). Skipped when no ``winter`` binary is discoverable.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import WorkspaceAcquisitionError


class _FakeWinter:
    def __init__(self) -> None:
        self.runs: list[list[str]] = []
        self.ready = 0

    def ensure_ready(self, workspace_root: Path) -> None:
        self.ready += 1

    def run(self, workspace_root: Path, args) -> None:  # type: ignore[no-untyped-def]
        self.runs.append(list(args))


class _FakeGit:
    def __init__(self) -> None:
        self.resets: list[tuple[str, str]] = []

    def reset_environment(self, env_workdir: Path, base_branch: str) -> None:
        self.resets.append((str(env_workdir), base_branch))


def _provider(root: str, pool, **kw):  # type: ignore[no-untyped-def]
    return WinterWorkspaceProvider(root, env_pool=pool, winter=_FakeWinter(), git=_FakeGit(), **kw)


@pytest.mark.unit
def test_acquire_prepares_a_free_env_and_returns_its_workdir(tmp_path: Path) -> None:
    winter, git = _FakeWinter(), _FakeGit()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=git)

    acquired = provider.acquire("ch_1", 1, held_ids=[])

    assert len(acquired) == 1
    assert acquired[0].environment_id == "e1"
    assert acquired[0].workdir == str(tmp_path / "e1")
    assert winter.runs == [["ws", "init", "e1"]]  # idempotent create/reapply
    assert git.resets == [(str(tmp_path / "e1"), "main")]  # reset-on-acquire


@pytest.mark.unit
def test_acquire_excludes_held_and_is_stateless(tmp_path: Path) -> None:
    provider = _provider(str(tmp_path), ["e1", "e2"])
    acquired = provider.acquire("ch_2", 1, held_ids=["e1"])
    assert [a.environment_id for a in acquired] == ["e2"]


@pytest.mark.unit
def test_acquire_refuses_all_or_nothing_when_pool_short(tmp_path: Path) -> None:
    provider = _provider(str(tmp_path), ["e1"])
    with pytest.raises(WorkspaceAcquisitionError):
        provider.acquire("ch_1", 1, held_ids=["e1"])  # the only env is held
    with pytest.raises(WorkspaceAcquisitionError):
        provider.acquire("ch_1", 2, held_ids=[])  # needs 2, pool has 1


@pytest.mark.unit
def test_release_is_a_noop(tmp_path: Path) -> None:
    provider = _provider(str(tmp_path), ["e1"])
    provider.release("e1")  # never raises, cleaning defers to next acquire


# --------------------------------------------------------------------------- #
# Component — the real winter CLI
# --------------------------------------------------------------------------- #

_WINTER = shutil.which("winter")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _make_workspace(tmp_path: Path) -> Path:
    """A minimal real winter workspace over one bare file:// origin with a main commit."""
    origin = tmp_path / "origins" / "toy.git"
    origin.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "README.md").write_text("# toy\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "origin", "main")

    workspace = tmp_path / "workspace"
    (workspace / ".winter").mkdir(parents=True)
    (workspace / ".winter" / "config.toml").write_text(
        'main_branch = "main"\n\n[[project_repository]]\nname = "toy"\nurl = "file://' + str(origin) + '"\n'
    )
    return workspace


@pytest.mark.component
@pytest.mark.skipif(_WINTER is None, reason="no `winter` binary discoverable for the real workspace drive")
def test_real_winter_acquire_returns_clean_worktree_and_resets(tmp_path: Path) -> None:
    assert _WINTER is not None  # narrowed by skipif
    workspace = _make_workspace(tmp_path)
    try:
        subprocess.run([_WINTER, "ws", "init"], cwd=str(workspace), check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:  # environment-specific winter failure
        pytest.skip(f"winter ws init failed in this environment: {exc.stderr or exc.stdout}")

    provider = WinterWorkspaceProvider(str(workspace), env_pool=["e1"], base_branch="main")
    acquired = provider.acquire("ch_1", 1, held_ids=[])
    assert len(acquired) == 1
    repo = Path(acquired[0].workdir) / "toy"
    assert (repo / "README.md").exists()  # a real, materialized worktree

    # Reset-on-acquire erases the previous tenant's dirt on the next acquire.
    (repo / "junk.txt").write_text("left behind")
    provider.acquire("ch_1", 1, held_ids=[])
    assert not (repo / "junk.txt").exists()
