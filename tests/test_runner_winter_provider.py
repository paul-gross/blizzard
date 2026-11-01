"""The winter workspace provider's allocation logic, unit-tested with fake winter/git sub-seams: pool selection,
all-or-nothing refusal, the reset-on-acquire sequence, and mid-reset failure attribution. The real ``winter`` CLI
drive is the winter-binding tier's (``tests/winter/``)."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import EnvironmentPreparationError, WorkspaceAcquisitionError


class _FakeWinter:
    def __init__(self, *, service_bound: bool = False, fail_on: list[str] | None = None) -> None:
        self.runs: list[list[str]] = []
        self.captures: list[list[str]] = []
        self.ready = 0
        self.service_bound = service_bound
        self.fail_on = fail_on  # arg-list prefix that raises, simulating a failed winter step
        # What `winter ws worktrees --json` reports; the last two shapes exist to be
        # filtered out.
        self.worktrees: list[dict[str, object]] = []

    def ensure_ready(self, workspace_root: Path) -> None:
        self.ready += 1

    def run(self, workspace_root: Path, args) -> None:
        args = list(args)
        if self.fail_on is not None and args[: len(self.fail_on)] == self.fail_on:
            raise RuntimeError("scripted winter failure")
        self.runs.append(args)

    def capture(self, workspace_root: Path, args) -> str:
        self.captures.append(list(args))
        if list(args)[:2] == ["ws", "worktrees"]:
            return json.dumps(self.worktrees)
        bound = "winter-service-tmux" if self.service_bound else None
        return json.dumps([{"slot": "service", "bound": bound, "binding_kind": "explicit"}])


class _FakeGit:
    def __init__(self, origins: dict[str, str] | None = None) -> None:
        self.cleans: list[str] = []
        self.origin_reads: list[str] = []
        self._origins = origins or {}

    def clean_environment(self, env_workdir: Path) -> None:
        self.cleans.append(str(env_workdir))

    def origin_url(self, repo_workdir: Path) -> str:
        self.origin_reads.append(str(repo_workdir))
        return self._origins.get(str(repo_workdir), f"file:///origins/{Path(repo_workdir).name}.git")


def _provider(root: str, pool, **kw):
    return WinterWorkspaceProvider(root, env_pool=pool, winter=_FakeWinter(), git=_FakeGit(), **kw)


@pytest.mark.unit
def test_acquire_of_a_fresh_env_inits_cleans_and_provisions(tmp_path: Path) -> None:
    """A pool env with no workdir yet is materialized by init — already clean, no reset steps."""
    winter, git = _FakeWinter(), _FakeGit()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=git)

    acquired = provider.acquire("ch_1", 1, held_ids=[])

    assert len(acquired) == 1
    assert acquired[0].environment_id == "e1"
    assert acquired[0].workdir == str(tmp_path / "e1")
    assert winter.runs == [
        ["ws", "pull", "--standalone"],  # once per pass, before any env
        ["ws", "init", "e1"],  # fresh env: init materializes it clean off the base
        ["provision", "e1"],  # no service-down — no orchestrator bound
    ]
    assert git.cleans == [str(tmp_path / "e1")]
    assert winter.captures == [["capabilities", "--json"]]  # the orchestrator probe


@pytest.mark.unit
def test_acquire_of_a_lived_in_env_runs_the_full_reset_sequence(tmp_path: Path) -> None:
    """An existing workdir gets the full reset: fetch, forced base checkout, disconnect, init."""
    winter, git = _FakeWinter(service_bound=True), _FakeGit()
    (tmp_path / "e1").mkdir()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], base_branch="master", winter=winter, git=git)

    provider.acquire("ch_1", 1, held_ids=[])

    assert winter.runs == [
        ["ws", "pull", "--standalone"],
        ["ws", "fetch", "e1"],
        ["ws", "checkout", "e1", "master", "--force"],
        ["ws", "disconnect", "e1"],
        ["ws", "init", "e1"],  # membership reconcile — safe only after the disconnect
        ["service", "down", "e1"],  # previous tenant's services die before reprovision
        ["provision", "e1"],
    ]
    assert git.cleans == [str(tmp_path / "e1")]


@pytest.mark.unit
def test_service_teardown_skipped_when_no_orchestrator_bound(tmp_path: Path) -> None:
    winter = _FakeWinter(service_bound=False)
    (tmp_path / "e1").mkdir()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=_FakeGit())

    provider.acquire("ch_1", 1, held_ids=[])

    assert ["service", "down", "e1"] not in winter.runs
    assert ["provision", "e1"] in winter.runs


@pytest.mark.unit
def test_standalones_refresh_once_per_pass_and_probe_is_cached(tmp_path: Path) -> None:
    """Workspace-global steps are hoisted out of the per-env path (no redundant repetition)."""
    winter = _FakeWinter()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1", "e2"], winter=winter, git=_FakeGit())

    provider.acquire("ch_1", 2, held_ids=[])

    assert winter.runs.count(["ws", "pull", "--standalone"]) == 1
    assert winter.runs[0] == ["ws", "pull", "--standalone"]  # before any per-env step
    assert winter.captures == [["capabilities", "--json"]]  # probed once, cached for e2


@pytest.mark.unit
def test_mid_reset_step_failure_is_attributed_to_step_and_env(tmp_path: Path) -> None:
    winter = _FakeWinter(fail_on=["ws", "disconnect"])
    (tmp_path / "e1").mkdir()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=_FakeGit())

    with pytest.raises(EnvironmentPreparationError) as excinfo:
        provider.acquire("ch_1", 1, held_ids=[])

    assert excinfo.value.step == "disconnect"
    assert excinfo.value.environment_id == "e1"
    assert isinstance(excinfo.value, WorkspaceAcquisitionError)  # FILL's all-or-nothing contract
    assert ["provision", "e1"] not in winter.runs  # aborted — no later step ran


@pytest.mark.unit
def test_acquire_excludes_held_and_is_stateless(tmp_path: Path) -> None:
    provider = _provider(str(tmp_path), ["e1", "e2"])
    acquired = provider.acquire("ch_2", 1, held_ids=["e1"])
    assert [a.environment_id for a in acquired] == ["e2"]


@pytest.mark.unit
def test_acquire_refuses_all_or_nothing_when_pool_short(tmp_path: Path) -> None:
    winter = _FakeWinter()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=_FakeGit())
    with pytest.raises(WorkspaceAcquisitionError):
        provider.acquire("ch_1", 1, held_ids=["e1"])  # the only env is held
    with pytest.raises(WorkspaceAcquisitionError):
        provider.acquire("ch_1", 2, held_ids=[])  # needs 2, pool has 1
    assert winter.runs == []  # refusal is decided before any reset step runs


@pytest.mark.unit
def test_release_is_a_noop(tmp_path: Path) -> None:
    provider = _provider(str(tmp_path), ["e1"])
    provider.release("e1")  # never raises, cleaning defers to next acquire


@pytest.mark.unit
def test_reacquire_refreshes_manifest_even_after_failed_reset(tmp_path: Path) -> None:
    (tmp_path / "e1").mkdir()
    winter = _FakeWinter()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=_FakeGit())

    def membership(repo: str) -> None:
        winter.worktrees = [{"kind": "worktree", "env": "e1", "repo": repo, "path": str(tmp_path / "e1" / repo)}]

    membership("old")
    assert [repo.name for repo in provider.repos("e1")] == ["old"]
    membership("new")
    provider.acquire("ch_1", 1, held_ids=[])
    assert [repo.name for repo in provider.repos("e1")] == ["new"]
    membership("latest")
    winter.fail_on = ["provision", "e1"]
    with pytest.raises(EnvironmentPreparationError):
        provider.acquire("ch_2", 1, held_ids=[])
    assert [repo.name for repo in provider.repos("e1")] == ["latest"]


@pytest.mark.unit
def test_manifest_read_waits_for_reset_before_caching(tmp_path: Path) -> None:
    (tmp_path / "e1").mkdir()
    entered = threading.Event()
    resume = threading.Event()

    class BlockingWinter(_FakeWinter):
        def run(self, workspace_root: Path, args) -> None:
            if list(args) == ["ws", "init", "e1"]:
                entered.set()
                assert resume.wait(timeout=5)
            super().run(workspace_root, args)

    winter = BlockingWinter()
    provider = WinterWorkspaceProvider(str(tmp_path), env_pool=["e1"], winter=winter, git=_FakeGit())
    names: list[str] = []
    reset = threading.Thread(target=lambda: provider.acquire("ch_1", 1, held_ids=[]))
    reset.start()
    assert entered.wait(timeout=5)
    read = threading.Thread(target=lambda: names.extend(repo.name for repo in provider.repos("e1")))
    read.start()
    try:
        assert not winter.captures or ["ws", "worktrees", "--json"] not in winter.captures
        winter.worktrees = [{"kind": "worktree", "env": "e1", "repo": "new", "path": str(tmp_path / "e1" / "new")}]
    finally:
        resume.set()
        reset.join(timeout=5)
        read.join(timeout=5)
    assert not reset.is_alive() and not read.is_alive()
    assert names == ["new"]


def _worktree_entry(env: str, repo: str, root: str) -> dict[str, object]:
    return {
        "kind": "worktree",
        "env": env,
        "repo": repo,
        "name": None,
        "label": f"{env}/{repo}",
        "path": f"{root}/{env}/{repo}",
    }


@pytest.mark.unit
def test_repos_reads_the_env_manifest_from_winter_rather_than_guessing_paths() -> None:
    """The manifest is *read*, not derived. Blizzard never joins an env workdir to a repo
    name to guess where a worktree is — winter is the authority on where it put things,
    and a repo's local directory name is free to differ from its remote's."""
    winter = _FakeWinter()
    winter.worktrees = [
        _worktree_entry("e1", "blizzard", "/ws"),
        _worktree_entry("e1", "blizzard-mock", "/ws"),
    ]
    git = _FakeGit({"/ws/e1/blizzard": "git@github.com:acme/blizzard.git"})
    provider = WinterWorkspaceProvider("/ws", env_pool=["e1"], winter=winter, git=git)

    bindings = provider.repos("e1")

    assert [(b.name, b.relpath, b.origin_url) for b in bindings] == [
        ("blizzard", "blizzard", "git@github.com:acme/blizzard.git"),
        ("blizzard-mock", "blizzard-mock", "file:///origins/blizzard-mock.git"),
    ]
    assert all(b.environment_id == "e1" for b in bindings)
    assert ["ws", "worktrees", "--json"] in winter.captures


@pytest.mark.unit
def test_repos_narrows_to_the_named_env_and_drops_standalone_clones() -> None:
    """A repo can be both a project repo and a standalone extension checkout with the
    same origin — only the env's own worktree belongs in the manifest."""
    winter = _FakeWinter()
    winter.worktrees = [
        _worktree_entry("e1", "blizzard-context", "/ws"),
        _worktree_entry("e2", "blizzard-context", "/ws"),
        {
            "kind": "standalone",
            "env": None,
            "repo": None,
            "name": "blizzard-context",
            "label": "blizzard-context",
            "path": "/ws/.winter/ext/harness",
        },
        {"kind": "workspace", "env": None, "repo": None, "name": "ws", "label": "<workspace>", "path": "/ws"},
    ]
    provider = WinterWorkspaceProvider("/ws", env_pool=["e1", "e2"], winter=winter, git=_FakeGit())

    assert [(b.environment_id, b.relpath) for b in provider.repos("e1")] == [("e1", "blizzard-context")]
    assert [(b.environment_id, b.relpath) for b in provider.repos("e2")] == [("e2", "blizzard-context")]


@pytest.mark.unit
def test_repos_is_empty_for_an_env_with_no_worktrees() -> None:
    """An empty manifest authorizes nothing, which is the safe reading — not an error
    the declare path has to special-case."""
    provider = WinterWorkspaceProvider("/ws", env_pool=["e1"], winter=_FakeWinter(), git=_FakeGit())

    assert provider.repos("e1") == []
