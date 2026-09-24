"""Folder-backed workspace binding: shared git clones and per-chunk worktrees."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path

from blizzard.runner.config import WorkspaceRepo
from blizzard.runner.environments.provider import (
    AcquiredEnvironment,
    EnvironmentPreparationError,
    IWorkspaceProvider,
    RepoBinding,
    WorkspaceAcquisitionError,
)

_MANAGED = ".blizzard-basic-env"
_ACTIVE = ".blizzard-active"
_REPOS_DIR = ".blizzard-manifests"


class BasicWorkspaceProvider:
    """Allocate clean worktrees, retaining released folders until capacity pressure.

    The store's held IDs are the capacity and (when injected) manifest authority.
    Direct consumers without a store use an active marker for manifest visibility.
    """

    def __init__(
        self,
        workspace_root: str,
        *,
        repos: Sequence[WorkspaceRepo],
        max_environments: int = 10,
        base_branch: str = "main",
        held_ids: Callable[[], list[str]] | None = None,
    ) -> None:
        self._root = Path(workspace_root).resolve()
        self._repos = tuple(repos)
        self._cap = max_environments
        self._branch = base_branch
        self._held_ids = held_ids

    def _git(self, cwd: Path, *args: str) -> str:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True)
        return result.stdout.strip()

    def _environments(self) -> list[Path]:
        if not self._root.exists():
            return []
        return [p for p in self._root.iterdir() if p.is_dir() and (p / _MANAGED).exists()]

    def _manifest_path(self, env: Path) -> Path:
        return self._root / _REPOS_DIR / f"{env.name}.json"

    def _remove(self, env: Path) -> None:
        projects = self._root / "projects"
        if projects.is_dir():
            # Include clones removed from the current config: their worktrees still
            # belong to this environment until its next acquisition or eviction.
            for clone in projects.iterdir():
                worktree = env / clone.name
                if not clone.is_dir() or not (clone / ".git").is_dir():
                    continue
                if worktree.exists() or worktree.is_symlink():
                    registered = any(
                        line == f"worktree {worktree}"
                        for line in self._git(clone, "worktree", "list", "--porcelain").splitlines()
                    )
                    if registered:
                        self._git(clone, "worktree", "remove", "--force", str(worktree))
                    elif worktree.is_dir() and not worktree.is_symlink():
                        shutil.rmtree(worktree)
                    else:
                        worktree.unlink()
                self._git(clone, "worktree", "prune")
        if env.exists():
            shutil.rmtree(env)
        self._manifest_path(env).unlink(missing_ok=True)

    def acquire(self, chunk_id: str, count: int, held_ids: list[str]) -> list[AcquiredEnvironment]:
        if not self._repos:
            raise WorkspaceAcquisitionError("basic workspace requires at least one [[workspace_repo]]")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", chunk_id) or chunk_id == "projects":
            raise WorkspaceAcquisitionError(f"unsafe chunk identifier {chunk_id!r}")
        if count < 0:
            raise WorkspaceAcquisitionError(f"invalid environment count {count}")
        held = set(held_ids)
        if len(held) + count > self._cap:
            raise WorkspaceAcquisitionError(
                f"environment cap {self._cap} exceeded: {len(held)} held, {count} requested"
            )
        self._root.mkdir(parents=True, exist_ok=True)
        existing = self._environments()
        # A prior release can be interrupted on either side of its store write.
        for env in existing:
            marker = env / _ACTIVE
            if env.name in held:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
        selected: list[Path] = []
        index = 0
        while len(selected) < count:
            index += 1
            name = chunk_id if index == 1 else f"{chunk_id}--{index}"
            candidate = self._root / name
            if candidate.name in held:
                continue
            if candidate.exists() and candidate not in existing:
                raise WorkspaceAcquisitionError(f"environment path already exists: {candidate}")
            if candidate in existing and (candidate / _MANAGED).read_text() != chunk_id:
                continue
            selected.append(candidate)
        # Refuse before touching any held environment. Reclaim the oldest released
        # worktrees only when a new allocation needs room.
        surplus = len(existing) + sum(p not in existing for p in selected) - self._cap
        evictable = sorted(
            (p for p in existing if p.name not in held and p not in selected), key=lambda p: p.stat().st_mtime
        )
        if surplus > len(evictable):
            raise WorkspaceAcquisitionError(f"environment cap {self._cap} reached")
        acquired: list[AcquiredEnvironment] = []
        try:
            for env in evictable[:surplus]:
                self._remove(env)
            for env in selected:
                self._prepare(env, chunk_id)
                acquired.append(AcquiredEnvironment(env.name, str(env)))
            for env in selected:
                (env / _ACTIVE).touch()
            return acquired
        except (OSError, subprocess.CalledProcessError, WorkspaceAcquisitionError) as exc:
            for env in selected:
                if (env / _MANAGED).exists():
                    # Preserve the preparation failure even if cleanup cannot finish.
                    with suppress(OSError, subprocess.CalledProcessError):
                        self._remove(env)
            failing = selected[len(acquired)] if len(acquired) < len(selected) else self._root
            raise EnvironmentPreparationError(
                f"basic workspace preparation failed for {failing.name}: {exc}",
                environment_id=failing.name,
                step="git-worktree",
            ) from exc

    def _prepare(self, env: Path, chunk_id: str) -> None:
        if (env / _MANAGED).exists():
            self._remove(env)
        env.mkdir(exist_ok=True)
        (env / _MANAGED).write_text(chunk_id)
        (env / _ACTIVE).unlink(missing_ok=True)
        projects = self._root / "projects"
        projects.mkdir(exist_ok=True)
        for repo in self._repos:
            clone = projects / repo.name
            if not clone.exists():
                self._git(projects, "clone", "--", repo.url, repo.name)
            elif self._git(clone, "remote", "get-url", "origin") != repo.url:
                raise WorkspaceAcquisitionError(f"clone origin changed for {repo.name!r}")
            self._git(clone, "fetch", "origin", self._branch)
            self._git(clone, "worktree", "prune")
            self._git(clone, "worktree", "add", "--detach", str(env / repo.name), f"origin/{self._branch}")
        self._manifest_path(env).parent.mkdir(exist_ok=True)
        self._manifest_path(env).write_text(json.dumps([{"name": repo.name, "url": repo.url} for repo in self._repos]))

    def release(self, environment_id: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", environment_id):
            return
        env = self._root / environment_id
        if (env / _MANAGED).exists():
            (env / _ACTIVE).unlink(missing_ok=True)

    def repos(self, environment_id: str) -> list[RepoBinding]:
        env = self._root / environment_id
        if env.name != environment_id or not (env / _MANAGED).is_file():
            return []
        if self._held_ids is not None and environment_id not in self._held_ids():
            return []
        if self._held_ids is None and not (env / _ACTIVE).is_file():
            return []
        try:
            recorded = json.loads(self._manifest_path(env).read_text())
        except (OSError, ValueError):
            return []
        if not isinstance(recorded, list):
            return []
        return [
            RepoBinding(
                environment_id,
                repo["name"],
                repo["name"],
                repo["url"],
            )
            for repo in recorded
            if isinstance(repo, dict)
            and isinstance(repo.get("name"), str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", repo["name"])
            and isinstance(repo.get("url"), str)
            and (env / repo["name"]).is_dir()
        ]


def _conforms_workspace_provider(provider: BasicWorkspaceProvider) -> IWorkspaceProvider:
    return provider
