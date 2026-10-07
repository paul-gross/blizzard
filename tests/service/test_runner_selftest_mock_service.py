"""The runner selftest against the real ``mock-claude-code``, service tier.

The selftest drives the real adapter through English prompts. The mock answers them only past its
fence, which needs the fence variable (carried by the worker passthrough) and a marker file in the
scratch repo's ancestry (carried by ``TMPDIR``, which the scratch repo is minted under). It is also
the drift guard for the identity the mock keys its responder on."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from blizzard.runner.harness.claude_code.adapter import ClaudeCodeAdapter
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.lifecycle.judgement.internal.elicitation_files import ElicitationFiles
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.selftest.checks import SelfTest
from blizzard.runner.selftest.internal.subprocess_scratch_git import SubprocessScratchGit
from tests.service.support import require_mock_fleet, service_gate

pytestmark = [pytest.mark.service, service_gate]

_FENCE_VAR = "BLIZZARD_MOCK_HARNESS_FENCE"


@pytest.fixture
def marked_tmpdir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``TMPDIR`` holding the fence marker, so every scratch repo minted under it is marked."""
    root = tmp_path / "runner-tmp"
    root.mkdir()
    (root / ".blizzard-mock-harness-fence").write_text("")
    monkeypatch.setenv("TMPDIR", str(root))
    monkeypatch.setattr(tempfile, "tempdir", None)  # re-read TMPDIR
    return root


@pytest.fixture
def executor() -> Iterator[ThreadPoolExecutor]:
    pool = ThreadPoolExecutor(max_workers=1)
    yield pool
    pool.shutdown(wait=True)


def _selftest(bin_dir: Path, passthrough: tuple[str, ...], executor: ThreadPoolExecutor) -> SelfTest:
    process = LinuxProcessProbe()
    adapter = ClaudeCodeAdapter(
        worker_env=AllowlistedEnv.of(passthrough),
        binary=str(bin_dir / "mock-claude-code"),
        model="claude-opus-5",
        process=process,
        launcher=ProcessLauncher(process, executor=executor),
    )
    return SelfTest(adapter, SubprocessScratchGit(), process, ElicitationFiles(root=""))


def test_a_fenced_selftest_passes_every_check_against_the_mock(
    marked_tmpdir: Path, monkeypatch: pytest.MonkeyPatch, executor: ThreadPoolExecutor
) -> None:
    bin_dir = require_mock_fleet()
    monkeypatch.setenv(_FENCE_VAR, "1")

    checks = _selftest(bin_dir, (_FENCE_VAR,), executor).run()

    assert len(checks) == 7
    assert [c for c in checks if not c.passed] == []


def test_a_selftest_without_the_fence_variable_in_the_passthrough_does_not_pass(
    marked_tmpdir: Path, monkeypatch: pytest.MonkeyPatch, executor: ThreadPoolExecutor
) -> None:
    bin_dir = require_mock_fleet()
    monkeypatch.setenv(_FENCE_VAR, "1")  # in the daemon's environ, but never named for the worker

    checks = _selftest(bin_dir, (), executor).run()

    assert any(not c.passed for c in checks)
