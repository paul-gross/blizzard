"""The OpenCode 2 declaration: the adapter it binds from the shared ``[opencode]`` section, ignoring ``auth_path`` and
``worker_config_path``, and a bundle, publish and scaffold carrying none of the runner's own files."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from blizzard.foundation.clock import SystemClock
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection
from blizzard.runner.harness.bundle import HarnessBundleError
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION, REQUEST_DIRNAME, driver_command
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.wiring import HarnessSections, first_bindings, publish_harness_bundle
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from tests.support.child_launch import CHILDREN

_SECTION = OpenCodeSection(
    enabled=True,
    binary="/opt/opencode",
    model_aliases=(("tier:fast", "openai/gpt-6-mini"),),
    auth_path="/nowhere/auth.json",
    worker_config_path="/nowhere/opencode.json",
)


def _shared(root: Path) -> SharedHarnessInputs:
    return SharedHarnessInputs(
        root=root,
        autonomy=Autonomy.Normal,
        config_dir=None,
        worker_env=AllowlistedEnv.of(()),
        transcripts_root="",
        runner_id="run_7",
    )


def test_the_binding_is_the_opencode_2_adapter_over_the_shared_section(tmp_path: Path) -> None:
    process = LinuxProcessProbe()
    with ThreadPoolExecutor(max_workers=1) as executor:
        binding = OPENCODE_V2_DECLARATION.binding(
            _SECTION,
            _shared(tmp_path),
            process=process,
            launcher=ProcessLauncher(process, executor=executor, children=CHILDREN),
            children=CHILDREN,
            clock=SystemClock(),
        )
    adapter = binding.adapter
    assert isinstance(adapter, OpenCodeTwoAdapter)
    assert isinstance(binding.transcript_source, OpenCodeTwoTranscriptSource)
    assert adapter.resolve_model(["tier:fast"]) == "openai/gpt-6-mini" and adapter.autonomy is Autonomy.Normal
    command = adapter.resume_command(str(tmp_path), "ses_1")
    assert "attend ses_1" in command and "--opencode /opt/opencode" in command and "/nowhere" not in command
    assert adapter._request_dir == tmp_path / REQUEST_DIRNAME  # pyright: ignore[reportPrivateUsage]
    assert adapter._runner_id == "run_7"  # pyright: ignore[reportPrivateUsage]


def test_the_driver_command_is_the_runners_own_entry_point() -> None:
    (entry,) = driver_command()
    assert Path(entry).name == "blizzard"


def test_scaffold_writes_nothing_and_publish_admits_no_opencode_entry(tmp_path: Path) -> None:
    OPENCODE_V2_DECLARATION.scaffold_runtime(_SECTION, tmp_path, worker_shell="/bin/sh")
    assert list(tmp_path.iterdir()) == []
    bundle = tmp_path / "bundle"
    (bundle / "opencode").mkdir(parents=True)
    root = tmp_path / "root"
    selection = BindingSelection({**first_bindings().chosen, "opencode": OPENCODE_V2_DECLARATION})
    sections = HarnessSections.defaults().replaced(_SECTION)
    publish_harness_bundle(bundle, root, sections=sections, selection=selection)
    (bundle / "opencode" / "opencode.json").write_text("{}")
    with pytest.raises(HarnessBundleError, match="not a recognized entry point"):
        publish_harness_bundle(bundle, root, sections=sections, selection=selection)
