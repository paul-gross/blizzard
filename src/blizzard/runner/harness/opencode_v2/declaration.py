"""The OpenCode 2 binding's declaration, the one approved site constructing :class:`OpenCodeTwoAdapter` and its health
probe. It reads the same ``[opencode]`` section as OpenCode 1, ignoring ``auth_path`` and ``worker_config_path``, and
publishes no worker config and no plugin: the operator's own service, configuration and credentials are used."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import HarnessLayout, HarnessSource
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.opencode_v2.service import ServiceKeeper
from blizzard.runner.harness.opencode_v2.transcript.normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold, admits_opencode_v2_version
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.probe import IProcessProbe

#: The bundle directory OpenCode shares across its major versions; on OpenCode 2 it holds nothing the runner reads.
OPENCODE_V2_BUNDLE_LAYOUT = HarnessLayout(dirname="opencode", entry_points=())
#: Where each turn's drive request waits for its driver, under the runner root.
REQUEST_DIRNAME = "opencode-drive"


class OpenCodeConfiguration(Protocol):
    """What this binding reads of the ``[opencode]`` section."""

    @property
    def binary(self) -> str: ...
    @property
    def model_aliases(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def effort_aliases(self) -> tuple[tuple[str, str], ...]: ...
    @property
    def supported_models(self) -> tuple[str, ...] | None: ...


def driver_command() -> tuple[str, ...]:
    """The runner's own ``blizzard`` entry point, beside the interpreter running it, so a worker's ``PATH`` need hold
    none."""
    script = Path(sys.executable).with_name("blizzard")
    return (str(script),) if script.is_file() else ("blizzard",)


@cache
def version_hold(binary: str) -> VersionHold:
    """The one hold ``binary``'s adapter and health probe share for this process."""
    return VersionHold()


def _keeper(section: OpenCodeConfiguration, worker_env: AllowlistedEnv, children: IChildProcesses) -> ServiceKeeper:
    return ServiceKeeper(section.binary, worker_env, children, UrllibServiceTransport())


@domain_model
@dataclass(frozen=True)
class OpenCodeTwoDeclaration:
    """OpenCode 2, declared once."""

    @property
    def harness_id(self) -> str:
        return OPENCODE_HARNESS_ID

    @property
    def normalizer_version(self) -> str:
        return NORMALIZER_VERSION

    @property
    def bundle_layout(self) -> HarnessLayout:
        return OPENCODE_V2_BUNDLE_LAYOUT

    @property
    def telemetry_names(self) -> HarnessTelemetryNames | None:
        return None

    def telemetry_plan(self, section: OpenCodeConfiguration, shared: SharedHarnessInputs) -> HarnessTelemetryPlan:
        return HarnessTelemetryPlan()

    def version_binary(self, section: OpenCodeConfiguration) -> str:
        return section.binary

    def admits_version(self, raw: str | None) -> bool:
        return admits_opencode_v2_version(raw)

    def binding(
        self,
        section: OpenCodeConfiguration,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
        children: IChildProcesses,
        clock: IClock,
    ) -> HarnessBinding:
        adapter = OpenCodeTwoAdapter(
            runner_id=shared.runner_id,
            worker_env=shared.worker_env,
            launcher=launcher,
            keeper=_keeper(section, shared.worker_env, children),
            versions=VersionProbe(children, shared.worker_env.tool()),
            hold=version_hold(section.binary),
            request_dir=shared.root / REQUEST_DIRNAME,
            clock=clock,
            binary=section.binary,
            driver_command=driver_command(),
            model_aliases=section.model_aliases,
            effort_aliases=section.effort_aliases,
            supported_models=section.supported_models,
            autonomy=shared.autonomy,
        )
        return HarnessBinding(adapter=adapter, transcript_source=adapter.transcript_source())

    def health_probe(
        self,
        section: OpenCodeConfiguration,
        shared: SharedHarnessInputs,
        *,
        spawn_root: str,
        corpus: ICompatibilityCorpus,
        children: IChildProcesses,
    ) -> IHarnessHealthProbe:
        return OpenCodeTwoHealthProbe(
            section.binary,
            keeper=_keeper(section, shared.worker_env, children),
            hold=version_hold(section.binary),
            corpus=corpus,
            versions=VersionProbe(children, shared.worker_env.tool()),
        )

    def publish_layout(
        self, section: OpenCodeConfiguration, *, autonomy: Autonomy, runtime_root: Path
    ) -> HarnessLayout:
        return OPENCODE_V2_BUNDLE_LAYOUT

    def scaffold_runtime(self, section: OpenCodeConfiguration, root: Path, *, worker_shell: str) -> None:
        return None

    def diagnostics(
        self, section: OpenCodeConfiguration, shared: SharedHarnessInputs, *, spawn_root: str
    ) -> tuple[str, ...]:
        return ()

    def unbundled_status(self, section: OpenCodeConfiguration) -> tuple[str, ...]:
        return ("opencode 2 runs on the operator's own service and configuration; the runner writes none",)

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        return ()


OPENCODE_V2_DECLARATION = OpenCodeTwoDeclaration()

__all__ = [
    "OPENCODE_V2_BUNDLE_LAYOUT",
    "OPENCODE_V2_DECLARATION",
    "REQUEST_DIRNAME",
    "OpenCodeConfiguration",
    "OpenCodeTwoDeclaration",
    "driver_command",
]
