"""The runner selftest and capability health against the engine-backed ``mock-opencode-2``, service tier.

The binding is composed by the production OpenCode 2 declaration, as ``blizzard runner selftest opencode`` composes it
on a runner whose binary is OpenCode 2, so the selftest's seven checks run through the OpenCode 2 adapter unchanged: each
turn a ``blizzard runner opencode drive`` on the managed service the mock registers under an isolated home. The mock
answers the selftest's prose prompts past its fence, which needs the fence variable in the worker passthrough and a
marker in the scratch repo's ancestry, carried by ``TMPDIR``."""

from __future__ import annotations

import subprocess
import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.runner.harness.adapter import IHarnessHealthProbe, IHarnessSelfTestSeam
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.internal.committed_corpus import CommittedCorpus
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION, version_hold
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.selftest.checks import SelfTest
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from tests.support.child_launch import CHILDREN, tool_env
from tests.support.mock_fleet import require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"
_TIERS = (("tier:deep", "openai/gpt-6-luna"),)


@pytest.fixture
def engine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """The emitted engine, under a home of its own that the runner, its drivers, and the service all resolve, and a
    ``TMPDIR`` holding the fence marker so every scratch repo minted under it is fenced; the service is stopped after."""
    bin_dir = require_mock_fleet()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".gitconfig").write_text("[user]\n\tname = selftest\n\temail = selftest@example.invalid\n")
    scratch = tmp_path / "runner-tmp"
    scratch.mkdir()
    (scratch / ".blizzard-mock-harness-fence").write_text("")
    for name in ("XDG_STATE_HOME", "XDG_DATA_HOME", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv(_FENCE_VAR, "1")
    monkeypatch.setenv("TMPDIR", str(scratch))
    monkeypatch.setattr(tempfile, "tempdir", None)  # re-read TMPDIR
    binary = tmp_path / "engine" / "opencode"
    binary.parent.mkdir()
    subprocess.run(
        [str(bin_dir / "mock-opencode-2"), "emit-engine", "--out", str(binary)],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    version_hold.cache_clear()
    yield binary
    subprocess.run([str(binary), "service", "stop"], capture_output=True, text=True, timeout=60)
    version_hold.cache_clear()


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=1)
    yield pool
    pool.shutdown(wait=True)


def _section(binary: Path, *, efforts: tuple[tuple[str, str], ...] = ()) -> OpenCodeSection:
    return OpenCodeSection(enabled=True, binary=str(binary), model_aliases=_TIERS, effort_aliases=efforts)


def _shared(root: Path) -> SharedHarnessInputs:
    return SharedHarnessInputs(
        root=root,
        autonomy=Autonomy.Dangerous,
        config_dir=None,
        worker_env=AllowlistedEnv.of((_FENCE_VAR,)),
        transcripts_root="",
        runner_id="run_selftest",
    )


def _adapter(binary: Path, root: Path, executor: ThreadPoolExecutor) -> IHarnessSelfTestSeam:
    process = LinuxProcessProbe()
    binding = OPENCODE_V2_DECLARATION.binding(
        _section(binary),
        _shared(root),
        process=process,
        launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
        children=CHILDREN,
        clock=SystemClock(),
    )
    assert binding.adapter is not None
    return binding.adapter


def _probe(binary: Path, root: Path, **section: tuple[tuple[str, str], ...]) -> IHarnessHealthProbe:
    return OPENCODE_V2_DECLARATION.health_probe(
        _section(binary, **section), _shared(root), spawn_root=str(root), corpus=CommittedCorpus(), children=CHILDREN
    )


def test_the_selftest_passes_every_check_against_the_mock_opencode_2(
    engine: Path, tmp_path: Path, executor: ThreadPoolExecutor
) -> None:
    adapter = _adapter(engine, tmp_path / "runner", executor)
    checks = SelfTest(
        adapter, SubprocessScratchGit(children=CHILDREN, env=tool_env()), LinuxProcessProbe(), ElicitationFiles(root="")
    ).run()

    assert len(checks) == 7
    assert [c for c in checks if not c.passed] == []


def test_health_starts_the_service_and_passes_every_check_on_its_listing(engine: Path, tmp_path: Path) -> None:
    probe = _probe(engine, tmp_path / "runner", efforts=(("high", "high"),))
    assert probe.binary_present() and probe.probe_authentication()
    assert probe.config_conflicts() == ()
    status = subprocess.run([str(engine), "service", "status"], capture_output=True, text=True, timeout=30)
    assert status.returncode == 0, "the probe started the managed service"


def test_health_names_a_variant_the_configured_model_does_not_offer(engine: Path, tmp_path: Path) -> None:
    (conflict,) = _probe(engine, tmp_path / "runner", efforts=(("max", "max"),)).config_conflicts()
    assert conflict == "tier tier:deep's model openai/gpt-6-luna offers no variant max"


def test_health_names_a_disabled_service_and_never_starts_it(engine: Path, tmp_path: Path) -> None:
    subprocess.run([str(engine), "service", "set", "disabled", "true"], check=True, capture_output=True, timeout=30)
    (conflict,) = _probe(engine, tmp_path / "runner").config_conflicts()
    assert "disabled" in conflict
    status = subprocess.run([str(engine), "service", "status"], capture_output=True, text=True, timeout=30)
    assert status.returncode != 0, "no service was started"
