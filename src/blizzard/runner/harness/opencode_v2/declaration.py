"""The OpenCode 2 binding's declaration, the one approved site constructing :class:`OpenCodeTwoAdapter` and its health
probe. It reads the same ``[opencode]`` section as OpenCode 1, ignoring ``auth_path`` and ``worker_config_path``, and
publishes no worker config and no plugin: the operator's own service, configuration and credentials are used."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.clock import IClock, IMonotonicClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import IVersionProbe
from blizzard.runner.harness.bundle import HarnessLayout, HarnessSource
from blizzard.runner.harness.declaration import SharedHarnessInputs
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus
from blizzard.runner.harness.opencode.section import OPENCODE_HARNESS_ID
from blizzard.runner.harness.opencode_v2.adapter import OpenCodeTwoAdapter
from blizzard.runner.harness.opencode_v2.driver import RegistrationSource
from blizzard.runner.harness.opencode_v2.health import OpenCodeTwoHealthProbe
from blizzard.runner.harness.opencode_v2.orphan import SessionOrphaner
from blizzard.runner.harness.opencode_v2.price_catalog import SqliteOpenCodeTwoPriceCatalog, resolve_catalog_store_path
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration, read_registration, registration_path
from blizzard.runner.harness.opencode_v2.service import UNLOCATED_DETAIL, ServiceKeeper, ServiceUnavailableError
from blizzard.runner.harness.opencode_v2.transcript.normalizer import NORMALIZER_VERSION
from blizzard.runner.harness.opencode_v2.transcript.source import OpenCodeTwoTranscriptSource
from blizzard.runner.harness.opencode_v2.transport import UrllibServiceTransport
from blizzard.runner.harness.opencode_v2.version_hold import VersionHold, admits_opencode_v2_version
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessAssembly, HarnessBinding
from blizzard.runner.harness.transcript import TranscriptErrorFactory
from blizzard.runner.process.child_env import AllowlistedEnv, ChildEnv
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


def snapshot_registration(tool_env: ChildEnv) -> RegistrationSource:
    """The registration ``tool_env`` locates, reread on every call; raises :class:`ServiceUnavailableError` when it
    locates none, so the keeper sharing that snapshot reports no service rather than start one."""

    def read() -> ServiceRegistration | None:
        path = registration_path(tool_env.variables)
        if path is None:
            raise ServiceUnavailableError(UNLOCATED_DETAIL)
        return read_registration(path)

    return read


def live_registration(worker_env: AllowlistedEnv) -> RegistrationSource:
    """The registration the worker's current tool profile locates, resolved afresh on every call; ``None`` when it
    locates none."""

    def read() -> ServiceRegistration | None:
        path = registration_path(worker_env.tool().variables)
        return read_registration(path) if path is not None else None

    return read


@domain_model
@dataclass(frozen=True)
class OpenCodeTwoDeclaration:
    """OpenCode 2, declared once."""

    @property
    def harness_id(self) -> str:
        return OPENCODE_HARNESS_ID

    @property
    def service_version(self) -> bool:
        return True

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

    def assemble(
        self,
        section: OpenCodeConfiguration,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
        children: IChildProcesses,
        clock: IClock,
        monotonic_clock: IMonotonicClock,
        health_roots: tuple[str, ...],
        corpus: ICompatibilityCorpus,
        versions: IVersionProbe | None = None,
    ) -> HarnessAssembly:
        """This runner's adapter and its one health probe, sharing one service keeper and one version hold — the
        hold refuses a version either observes for as long as this runner's composition lasts. The keeper, the
        transcript source and the orphaner share one transport and ``monotonic_clock``; the keeper reads registration
        through its own tool-profile snapshot, the other two through the worker's live profile."""
        del process, health_roots
        transport = UrllibServiceTransport()
        tool_env = shared.worker_env.tool()
        keeper = ServiceKeeper(
            section.binary,
            tool_env,
            children,
            transport,
            source=snapshot_registration(tool_env),
            monotonic_clock=monotonic_clock,
        )
        registration = live_registration(shared.worker_env)
        transcript_source = OpenCodeTwoTranscriptSource(
            registration,
            transport,
            TranscriptErrorFactory(get_logger("blizzard.runner.harness.transcript")),
            runner_id=shared.runner_id,
            monotonic_clock=monotonic_clock,
        )
        orphaner = SessionOrphaner(registration, transport, monotonic_clock=monotonic_clock)
        hold = VersionHold()
        store_path = resolve_catalog_store_path(shared.worker_env.variables)
        adapter = OpenCodeTwoAdapter(
            runner_id=shared.runner_id,
            worker_env=shared.worker_env,
            launcher=launcher,
            keeper=keeper,
            versions=versions or VersionProbe(children, shared.worker_env.tool()),
            hold=hold,
            request_dir=shared.root / REQUEST_DIRNAME,
            clock=clock,
            transcript_source=transcript_source,
            transcript_root=shared.transcripts_root,
            bundle_path=str(shared.bundle.path) if shared.bundle is not None else None,
            orphaner=orphaner,
            binary=section.binary,
            driver_command=driver_command(),
            model_aliases=section.model_aliases,
            effort_aliases=section.effort_aliases,
            supported_models=section.supported_models,
            autonomy=shared.autonomy,
            price_catalog=SqliteOpenCodeTwoPriceCatalog(store_path) if store_path is not None else None,
        )
        probe = OpenCodeTwoHealthProbe(
            section.binary,
            keeper=keeper,
            hold=hold,
            corpus=corpus,
            versions=versions or VersionProbe(children, shared.worker_env.tool()),
            model_aliases=section.model_aliases,
            effort_aliases=section.effort_aliases,
        )
        return HarnessAssembly(
            binding=HarnessBinding(adapter=adapter, transcript_source=transcript_source),
            health_probes=(probe,),
        )

    @property
    def health_varies_by_spawn_root(self) -> bool:
        return False

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
    "live_registration",
    "snapshot_registration",
]
