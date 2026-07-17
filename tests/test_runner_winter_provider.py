"""The winter workspace provider — allocation logic (unit) and the real CLI drive (component).

The allocation contract is unit-tested with fake winter/git sub-seams: pick
from the pool minus the held set, all-or-nothing refusal, the full reset-on-acquire
sequence (D-021: standalones once per pass, then fetch → forced base checkout →
disconnect → membership reconcile → clean → service teardown → reprovision), the
orchestrator probe, and mid-reset failure attribution. The component tests drive the
**real** ``winter`` CLI + git against a minimal real workspace over ``file://``
origins — including the stale-feature-branch reproduction (one repo connected to a
feature branch that exists only for it, siblings unconnected) that used to stall
``winter ws init`` with ``set-upstream-to … exit 128``. Skipped when no enclosing
winter workspace is available to clone the framework from.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from blizzard.runner.environments.internal.winter_cli import SubprocessWinterCli, WinterCliError
from blizzard.runner.environments.internal.winter_provider import WinterWorkspaceProvider
from blizzard.runner.environments.provider import EnvironmentPreparationError, WorkspaceAcquisitionError


class _FakeWinter:
    def __init__(self, *, service_bound: bool = False, fail_on: list[str] | None = None) -> None:
        self.runs: list[list[str]] = []
        self.captures: list[list[str]] = []
        self.ready = 0
        self.service_bound = service_bound
        self.fail_on = fail_on  # arg-list prefix that raises, simulating a failed winter step

    def ensure_ready(self, workspace_root: Path) -> None:
        self.ready += 1

    def run(self, workspace_root: Path, args) -> None:  # type: ignore[no-untyped-def]
        args = list(args)
        if self.fail_on is not None and args[: len(self.fail_on)] == self.fail_on:
            raise RuntimeError("scripted winter failure")
        self.runs.append(args)

    def capture(self, workspace_root: Path, args) -> str:  # type: ignore[no-untyped-def]
        self.captures.append(list(args))
        bound = "winter-service-tmux" if self.service_bound else None
        return json.dumps([{"slot": "service", "bound": bound, "binding_kind": "explicit"}])


class _FakeGit:
    def __init__(self) -> None:
        self.cleans: list[str] = []

    def clean_environment(self, env_workdir: Path) -> None:
        self.cleans.append(str(env_workdir))


def _provider(root: str, pool, **kw):  # type: ignore[no-untyped-def]
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


# --------------------------------------------------------------------------- #
# Component — the real winter CLI
# --------------------------------------------------------------------------- #


def _enclosing_winter_workspace() -> Path | None:
    """The winter workspace this repo develops inside, found by the shim's own rule.

    The fixture workspace is minted the way ``blizzard-mock`` mints one: clone this
    local workspace (its committed master ships ``tools/winter-cli``), then swap in
    the test's own config — so the component tier drives the vendored CLI shape
    ``SubprocessWinterCli`` prefers, with root resolution landing on the fixture.
    """
    for directory in Path(__file__).resolve().parents:
        if (directory / ".winter" / "config.toml").is_file() and (directory / "tools" / "winter-cli").is_dir():
            return directory
    return None


_WINTER_SOURCE = _enclosing_winter_workspace()


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


def _git_out(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def _make_workspace(tmp_path: Path, repos: tuple[str, ...] = ("toy",)) -> Path:
    """A real winter workspace (cloned framework) over bare file:// origins with a main commit each."""
    entries: list[str] = ['main_branch = "main"']
    for name in repos:
        origin = tmp_path / "origins" / f"{name}.git"
        origin.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True)
        seed = tmp_path / "seeds" / name
        seed.mkdir(parents=True)
        _git(seed, "init", "-b", "main")
        _git(seed, "config", "user.email", "t@t")
        _git(seed, "config", "user.name", "t")
        (seed / "README.md").write_text(f"# {name}\n")
        _git(seed, "add", "-A")
        _git(seed, "commit", "-m", "seed")
        _git(seed, "remote", "add", "origin", str(origin))
        _git(seed, "push", "origin", "main")
        entries.append(f'[[project_repository]]\nname = "{name}"\nurl = "file://{origin}"')

    assert _WINTER_SOURCE is not None  # narrowed by skipif
    workspace = tmp_path / "workspace"
    subprocess.run(
        ["git", "clone", "--quiet", str(_WINTER_SOURCE), str(workspace)], check=True, capture_output=True, text=True
    )
    (workspace / ".winter" / "config.toml").write_text("\n\n".join(entries) + "\n")
    lock = workspace / ".winter" / "config.lock"
    if lock.exists():
        lock.unlink()
    return workspace


def _init_workspace_or_skip(workspace: Path) -> None:
    """Workspace-level init (source checkouts off the file:// origins) via the vendored CLI."""
    cli = SubprocessWinterCli()
    try:
        cli.ensure_ready(workspace)
        cli.run(workspace, ["ws", "init"])
    except WinterCliError as exc:  # environment-specific winter failure
        pytest.skip(f"winter ws init failed in this environment: {exc}")


@pytest.mark.component
@pytest.mark.skipif(_WINTER_SOURCE is None, reason="no enclosing winter workspace to clone the framework from")
def test_real_winter_acquire_returns_clean_worktree_and_resets(tmp_path: Path) -> None:
    workspace = _make_workspace(tmp_path)
    _init_workspace_or_skip(workspace)

    provider = WinterWorkspaceProvider(str(workspace), env_pool=["e1"], base_branch="main")
    acquired = provider.acquire("ch_1", 1, held_ids=[])
    assert len(acquired) == 1
    repo = Path(acquired[0].workdir) / "toy"
    assert (repo / "README.md").exists()  # a real, materialized worktree

    # Reset-on-acquire erases the previous tenant's dirt on the next acquire.
    (repo / "junk.txt").write_text("left behind")
    (repo / "README.md").write_text("dirtied")
    provider.acquire("ch_1", 1, held_ids=[])
    assert not (repo / "junk.txt").exists()
    assert (repo / "README.md").read_text() == "# toy\n"


@pytest.mark.component
@pytest.mark.skipif(_WINTER_SOURCE is None, reason="no enclosing winter workspace to clone the framework from")
def test_real_winter_acquire_recovers_stale_feature_branch_tracking(tmp_path: Path) -> None:
    """The r1 stall, reproduced and fixed: a previous tenant left one repo connected to a
    feature branch that exists only for it while its sibling sits unconnected — the state
    that made init-first preparation die on ``set-upstream-to … exit 128`` (issue #16)."""
    workspace = _make_workspace(tmp_path, repos=("toy-a", "toy-b"))
    _init_workspace_or_skip(workspace)

    provider = WinterWorkspaceProvider(str(workspace), env_pool=["e1"], base_branch="main")
    workdir = Path(provider.acquire("ch_1", 1, held_ids=[])[0].workdir)
    repo_a, repo_b = workdir / "toy-a", workdir / "toy-b"

    # The previous tenant: toy-a delivered from a feature branch (pushed, upstream set —
    # the ref exists only for toy-a); toy-b left unconnected, dirty, with untracked junk.
    _git(repo_a, "config", "user.email", "t@t")
    _git(repo_a, "config", "user.name", "t")
    _git(repo_a, "checkout", "-b", "fix/stale")
    (repo_a / "work.txt").write_text("wip")
    _git(repo_a, "add", "-A")
    _git(repo_a, "commit", "-m", "wip")
    _git(repo_a, "push", "-u", "origin", "fix/stale")
    (repo_b / "README.md").write_text("dirtied")
    (repo_b / "junk.txt").write_text("left behind")

    # Re-acquire must succeed — this is exactly where the old init-first flow stalled.
    reacquired = provider.acquire("ch_2", 1, held_ids=[])
    assert Path(reacquired[0].workdir) == workdir

    for repo, readme in ((repo_a, "# toy-a\n"), (repo_b, "# toy-b\n")):
        # Disconnected: no worktree retains feature-branch upstream tracking.
        assert _git_out(repo, "rev-parse", "--abbrev-ref", "@{u}").returncode != 0
        # Forced to origin/main and clean: no dirty or untracked files survive.
        assert _git_out(repo, "status", "--porcelain").stdout.strip() == ""
        assert (repo / "README.md").read_text() == readme
    assert not (repo_a / "work.txt").exists()
    assert not (repo_b / "junk.txt").exists()
