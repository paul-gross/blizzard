"""``blizzard runner add`` and ``retire`` over a registered host directory (component tier): a runner added under the
host's own credential with its token, directory and ``[[runner]]`` written in that order; one retired at the hub before
its entry is removed; and both refused, reaching no hub, on a host holding no host token. A registered host's
``tick`` drives its runner as one the host reports for."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from click.testing import Result

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import SystemClock
from blizzard.foundation.runner_token_names import HOST_TOKEN_VARIABLE
from blizzard.runner.cli.runtime import named_runners
from blizzard.runner.composition import RunnerProcess
from blizzard.runner.host_config import BuiltRunner
from blizzard.runner.host_layout import HostLayout, HostLock
from blizzard.runner.hub.client import HostRunnerRetireRefused
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.hub.token_file import ENV_FILENAME
from blizzard.runner.loop_wiring import LoopWiring
from tests.config_inputs import load_host_config
from tests.runner_init_fakes import FakeInitHub, init_runner

pytestmark = pytest.mark.component

_HUB = "http://hub.test:8421"


def _run(hub: FakeInitHub, *args: str) -> Result:
    return init_runner(hub).invoke(blizzard, ["runner", *args])


def _registered(tmp_path: Path, hub: FakeInitHub) -> Path:
    root = tmp_path / "host"
    result = _run(hub, "init", str(root), "--hub", _HUB)
    assert result.exit_code == 0, result.output
    return root


def _runner_names(root: Path) -> list[str]:
    return [r["name"] for r in tomllib.loads(HostLayout(root).config_path.read_text()).get("runner", [])]


def _unregistered(root: Path) -> None:
    env = root / ENV_FILENAME
    env.write_text(
        "".join(line for line in env.read_text().splitlines(keepends=True) if HOST_TOKEN_VARIABLE not in line)
    )


def test_add_mints_the_runner_under_the_host_and_writes_its_token_directory_and_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1", "rn_init2"]}
    assert HubTokenFile.of(root, "BZ_HUB_TOKEN_R2").held() == "init-token-2"
    assert (root / "runners" / "r2").is_dir()
    assert _runner_names(root) == ["runner-local", "r2"]
    host = load_host_config(root)
    added = host.runners[1]
    assert (added.id, added.subscription) == ("rn_init2", host.runners[0].subscription)
    assert [(b.project, b.workspace, b.agents) for b in added.bindings] == [("default", "workspace-local", None)]
    assert added.own is not None and added.own.agents == 1
    assert all(isinstance(p, BuiltRunner) for p in host.projections())
    assert "starts" not in result.output  # no host is running here


def test_add_binds_each_project_to_its_workspace_and_sums_per_project_agents_into_the_runner_count(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(
        fake_init_hub,
        "add",
        "r2",
        "--project",
        "p1",
        "--project",
        "p2",
        "--agents",
        "2",
        "--agents",
        "3",
        "--dir",
        str(root),
    )

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.declared[-1] == ("p1", "p2")
    added = load_host_config(root).runners[1]
    assert [(b.project, b.workspace, b.agents) for b in added.bindings] == [
        ("p1", "workspace-local", None),
        ("p2", "workspace-local", None),
    ]
    assert added.own is not None and added.own.agents == 5


def test_add_with_one_agents_value_sets_the_runner_count_for_every_project(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)

    result = _run(fake_init_hub, "add", "r2", "--project", "p1", "--project", "p2", "--agents", "4", "--dir", str(root))

    assert result.exit_code == 0, result.output
    added = load_host_config(root).runners[1]
    assert added.own is not None and added.own.agents == 4


@pytest.mark.parametrize(
    ("args", "said"),
    [
        (("r2", "--project", "p", "--workspace", "elsewhere"), "--workspace 'elsewhere' is not a workspace"),
        (("runner-local", "--project", "p"), "already holds a runner named 'runner-local'"),
        (("RUNNER_LOCAL", "--project", "p"), "BZ_HUB_TOKEN_RUNNER_LOCAL"),
        (("r2", "--project", "p", "--project", "q", "--agents", "1", "--agents", "2", "--agents", "3"), "--agents"),
    ],
)
def test_add_refuses_what_the_host_file_does_not_allow_before_reaching_the_hub(
    tmp_path: Path, fake_init_hub: FakeInitHub, args: tuple[str, ...], said: str
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    before = (HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes())

    result = _run(fake_init_hub, "add", *args, "--dir", str(root))

    assert result.exit_code != 0
    assert said in result.output
    assert len(fake_init_hub.admin.added) == 1
    assert (HostLayout(root).config_path.read_bytes(), (root / ENV_FILENAME).read_bytes()) == before


@pytest.mark.parametrize("verb", [("add", "r2", "--project", "p"), ("retire", "--runner", "runner-local")])
def test_add_and_retire_refuse_a_host_holding_no_host_token_naming_how_to_add_one(
    tmp_path: Path, fake_init_hub: FakeInitHub, verb: tuple[str, ...]
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    _unregistered(root)
    before = HostLayout(root).config_path.read_bytes()

    result = _run(fake_init_hub, *verb, "--dir", str(root))

    assert result.exit_code != 0
    assert "blizzard hub runner-host add" in result.output
    assert HostLayout(root).config_path.read_bytes() == before
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}


def test_add_on_a_running_host_says_it_starts_the_runner_at_the_next_start(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert "it starts runner r2 at its next start" in result.output


def test_retire_retires_at_the_hub_then_removes_the_entry(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    with HostLock.acquire(HostLayout(root)):
        result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert fake_init_hub.admin.held == {"hs_init1": ["rn_init1"]}
    assert _runner_names(root) == ["runner-local"]
    assert "it halts runner r2 at its next tick" in result.output


def test_retire_names_the_runner_by_id_too(tmp_path: Path, fake_init_hub: FakeInitHub) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0

    result = _run(fake_init_hub, "retire", "--runner", "rn_init2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_holding_chunks_is_refused_leaving_its_entry_and_names_force(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.retire_refusal = HostRunnerRetireRefused("409", detail="runner holds ch_1")

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "ch_1" in result.output and "--force" in result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_of_a_runner_not_under_the_host_is_refused_leaving_its_entry(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    fake_init_hub.admin.held["hs_init1"].clear()

    result = _run(fake_init_hub, "retire", "--runner", "runner-local", "--dir", str(root))

    assert result.exit_code != 0
    assert "does not stand under this host" in result.output
    assert _runner_names(root) == ["runner-local"]


def test_retire_forgets_the_runners_identity_so_init_runs_again_over_the_remaining_runner(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    rerun = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert rerun.exit_code == 0, rerun.output
    assert set(named_runners(load_host_config(root), SystemClock())) == {"rn_init1", "rn_init2"}

    result = _run(fake_init_hub, "retire", "--runner", "r2", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert named_runners(load_host_config(root), SystemClock()) == ("rn_init1",)
    again = _run(fake_init_hub, "init", str(root), "--hub", _HUB)
    assert again.exit_code == 0, again.output


def test_a_readd_of_one_runner_beside_another_refuses_rather_than_set_the_shared_store_aside(
    tmp_path: Path, fake_init_hub: FakeInitHub
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    assert _run(fake_init_hub, "add", "r2", "--project", "default", "--dir", str(root)).exit_code == 0
    assert _run(fake_init_hub, "init", str(root), "--hub", _HUB).exit_code == 0
    env = root / ENV_FILENAME
    env.write_text("".join(line for line in env.read_text().splitlines(keepends=True) if "BZ_HUB_TOKEN_R2" not in line))
    store = HostLayout(root).store_path
    before = (named_runners(load_host_config(root), SystemClock()), HostLayout(root).config_path.read_bytes())

    refused = _run(fake_init_hub, "init", str(root), "--hub", _HUB, "--allow-readd")

    assert refused.exit_code != 0
    assert "runner-local" in refused.output and "blizzard runner retire --runner r2" in refused.output
    assert (named_runners(load_host_config(root), SystemClock()), HostLayout(root).config_path.read_bytes()) == before
    assert not [path for path in store.parent.iterdir() if path.name.startswith(f"{store.name}.")]
    assert len(fake_init_hub.admin.added) == 2


def test_tick_on_a_registered_host_drives_its_runner_as_one_the_host_reports(
    tmp_path: Path, fake_init_hub: FakeInitHub, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _registered(tmp_path, fake_init_hub)
    seen: list[bool] = []

    def tick_once(_wiring: LoopWiring, *, process: RunnerProcess | None = None) -> None:
        assert process is not None
        seen.append(process.host.registered())

    monkeypatch.setattr(LoopWiring, "tick_once", tick_once)

    result = _run(fake_init_hub, "tick", "--dir", str(root))

    assert result.exit_code == 0, result.output
    assert seen == [True]
