"""Service tier: how long a runner holds a foreign OpenCode service version, against real ``mock-opencode-2
emit-engine`` artifacts registered under isolated homes. Each runner is one production OpenCode 2 assembly; two over
the same binary keep their own holds whether they find different registrations or the same service, a hold outlives a
healthy return and a changed registration, and only composing that runner again releases it. Versions change by
replacing the one binary with an artifact baked at another version and restarting its service under it."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock, SystemMonotonicClock
from blizzard.runner.environments.provider import AcquiredEnvironment
from blizzard.runner.harness.adapter import HarnessVersionChangedError, WorkerPreamble
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.opencode_v2.registration import read_registration
from blizzard.runner.harness.opencode_v2.service import ServiceKeeper
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import CHILDREN
from tests.support.mock_fleet import require_mock_fleet, service_gate
from tests.support.runner_fakes import make_envelope

pytestmark = [pytest.mark.service, service_gate]

_ADMITTED, _FOREIGN = "2.1.4", "3.0.0"
_XDG = ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME")


@dataclass
class Artifacts:
    """The one binary every runner names, and the artifacts baked at each version that replace it."""

    binary: Path
    baked: dict[str, Path]

    def become(self, version: str) -> None:
        """Make the binary the artifact baked at ``version``, replacing the file a running service was started from
        rather than writing into it."""
        staged = self.binary.with_suffix(".staged")
        shutil.copy2(self.baked[version], staged)
        os.replace(staged, self.binary)

    def service(self, home: Path, *args: str) -> None:
        env = {"PATH": "/usr/bin:/bin", "HOME": str(home)}
        ran = subprocess.run([str(self.binary), "service", *args], env=env, capture_output=True, text=True, timeout=60)
        assert ran.returncode == 0, ran.stderr

    def restart_as(self, home: Path, version: str) -> None:
        self.service(home, "stop")
        self.become(version)
        self.service(home, "start")


@pytest.fixture
def artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Artifacts]:
    mock = require_mock_fleet() / "mock-opencode-2"
    if not mock.is_file():
        pytest.skip("the provisioned sibling blizzard-mock has no mock-opencode-2 (run `winter provision <env>`)")
    for name in _XDG:
        monkeypatch.delenv(name, raising=False)
    baked: dict[str, Path] = {}
    for version in (_ADMITTED, _FOREIGN):
        out = tmp_path / "baked" / version / "opencode"
        out.parent.mkdir(parents=True)
        subprocess.run(
            [str(mock), "emit-engine", "--out", str(out), "--version", version],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        baked[version] = out
    binary = tmp_path / "bin" / "opencode"
    binary.parent.mkdir()
    made = Artifacts(binary, baked)
    made.become(_ADMITTED)
    yield made
    for home in tmp_path.glob("home-*"):
        subprocess.run(
            [str(binary), "service", "stop"],
            env={"PATH": "/usr/bin:/bin", "HOME": str(home)},
            capture_output=True,
            timeout=60,
        )


def _home(tmp_path: Path, name: str) -> Path:
    home = tmp_path / f"home-{name}"
    home.mkdir()
    return home


@dataclass
class Runner:
    """One runner's OpenCode 2 assembly: its adapter and its health probe."""

    adapter: OpenCodeTwoAdapter
    probe: OpenCodeTwoHealthProbe
    root: Path

    def refused_by_health(self) -> bool:
        conflicts = self.probe.config_conflicts()
        return any("restart the runner" in conflict for conflict in conflicts)

    def refused_by_launch(self) -> bool:
        """Whether a spawn is refused for the held version; only ever called where it is, so no driver launches."""
        preamble = WorkerPreamble(
            environments=[AcquiredEnvironment(environment_id="e1", workdir=str(self.root))],
            lease_id="lease_1",
            local_api_url="http://127.0.0.1:1",
        )
        try:
            self.adapter.spawn(make_envelope("chunk_1", "build", node_id="node_1", choices=[]), preamble, None)
        except HarnessVersionChangedError:
            return True
        raise AssertionError("a launch the hold should have refused was not")


def _compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, binary: Path, home: Path, executor: ThreadPoolExecutor, name: str
) -> Runner:
    """A runner composed while ``home`` is the daemon's home, so its keeper's snapshot locates that home's service."""
    monkeypatch.setenv("HOME", str(home))
    root = tmp_path / f"runner-{name}"
    root.mkdir(exist_ok=True)
    process = LinuxProcessProbe()
    assembly = OPENCODE_V2_DECLARATION.assemble(
        OpenCodeSection(enabled=True, binary=str(binary)),
        SharedHarnessInputs(
            root=root,
            autonomy=Autonomy.Dangerous,
            config_dir=None,
            worker_env=AllowlistedEnv.of(()),
            transcripts_root="",
            runner_id=f"run_{name}",
        ),
        process=process,
        launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
        children=CHILDREN,
        clock=SystemClock(),
        monotonic_clock=SystemMonotonicClock(),
        health_roots=(str(root),),
        corpus=CommittedCorpus(),
    )
    adapter = assembly.binding.adapter
    (probe,) = assembly.health_probes
    assert isinstance(adapter, OpenCodeTwoAdapter) and isinstance(probe, OpenCodeTwoHealthProbe)
    return Runner(adapter, probe, root)


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=1)
    yield pool
    pool.shutdown(wait=True)


def _registration_id(home: Path) -> str:
    registration = read_registration(home / ".local" / "state" / "opencode" / "service.json")
    assert registration is not None
    return registration.id


def test_a_hold_poisoned_through_the_adapter_refuses_its_probe_and_spares_a_runner_on_the_same_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifacts: Artifacts, executor: ThreadPoolExecutor
) -> None:
    home = _home(tmp_path, "shared")
    artifacts.service(home, "start")
    a = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "a")
    b = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "b")
    assert a.probe.config_conflicts() == () and b.probe.config_conflicts() == ()

    artifacts.restart_as(home, _FOREIGN)
    assert a.adapter.observe_version() == _FOREIGN
    assert a.refused_by_health() and a.refused_by_launch()

    before = _registration_id(home)
    artifacts.restart_as(home, _ADMITTED)
    assert _registration_id(home) != before
    # A healthy return on a new registration leaves A's hold standing; B never observed the foreign service.
    assert a.refused_by_health() and a.refused_by_launch()
    assert b.probe.config_conflicts() == ()
    assert b.adapter.observe_version() == _ADMITTED


def test_a_hold_poisoned_through_health_refuses_the_adapter_until_that_runner_alone_is_recomposed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifacts: Artifacts, executor: ThreadPoolExecutor
) -> None:
    home = _home(tmp_path, "shared")
    artifacts.service(home, "start")
    a = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "a")
    b = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "b")

    artifacts.restart_as(home, _FOREIGN)
    assert a.refused_by_health() and a.refused_by_launch()
    # The currently unsupported service is refused by every runner that observes it, each for itself.
    assert b.refused_by_launch() and b.refused_by_health()

    artifacts.restart_as(home, _ADMITTED)
    recomposed = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "a")
    assert recomposed.probe.config_conflicts() == ()
    assert recomposed.adapter.observe_version() == _ADMITTED
    assert b.refused_by_health() and b.refused_by_launch()
    assert a.refused_by_health()


def test_runners_on_different_registrations_of_the_same_binary_hold_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifacts: Artifacts, executor: ThreadPoolExecutor
) -> None:
    home_a, home_b = _home(tmp_path, "a"), _home(tmp_path, "b")
    artifacts.service(home_a, "start")
    a = _compose(tmp_path, monkeypatch, artifacts.binary, home_a, executor, "a")
    artifacts.become(_FOREIGN)
    artifacts.service(home_b, "start")
    b = _compose(tmp_path, monkeypatch, artifacts.binary, home_b, executor, "b")
    artifacts.become(_ADMITTED)

    assert b.adapter.observe_version() == _FOREIGN
    assert b.refused_by_health() and b.refused_by_launch()
    assert a.adapter.observe_version() == _ADMITTED
    assert a.probe.config_conflicts() == ()


def test_one_assemblys_shared_urllib_transport_answers_concurrent_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, artifacts: Artifacts, executor: ThreadPoolExecutor
) -> None:
    home = _home(tmp_path, "shared")
    artifacts.service(home, "start")
    runner = _compose(tmp_path, monkeypatch, artifacts.binary, home, executor, "a")
    keeper = runner.adapter._keeper  # pyright: ignore[reportPrivateUsage]
    source = runner.adapter.transcript_source()
    assert isinstance(keeper, ServiceKeeper) and isinstance(source, OpenCodeTwoTranscriptSource)
    assert source._transport is keeper._transport  # pyright: ignore[reportPrivateUsage]
    calls: list[Callable[[], object]] = [
        runner.adapter.observe_version,
        keeper.answering,
        keeper.listed_models,
        lambda: source.tail_position("ses_absent", spawn_cwd=None),
    ]

    def call(n: int) -> object:
        return calls[n % len(calls)]()

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(call, range(64)))
    expected = [calls[n]() for n in range(len(calls))]
    assert results == [expected[n % len(calls)] for n in range(64)]
    assert expected[0] == _ADMITTED and expected[1] is not None and expected[2]
