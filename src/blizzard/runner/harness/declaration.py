"""The construction half of a harness declaration — what a binding builds or reports from its
parsed section (:mod:`.sections`) plus the runner-wide inputs every binding shares. A consumer
iterates :data:`~blizzard.runner.harness.wiring.HARNESS_CATALOG` and never names a binding;
adding a harness is one declaration and one section kind under ``harness/internal/``."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from blizzard.foundation.roles import dto
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource
from blizzard.runner.harness.claude_code.telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.harness.sections import IHarnessSection
from blizzard.runner.process.probe import IProcessProbe

SectionT_contra = TypeVar("SectionT_contra", bound=IHarnessSection, contravariant=True)


@dto
@dataclass(frozen=True)
class SharedHarnessInputs:
    """The runner-wide knobs every binding reads — never carried by a binding's own section."""

    #: The runner runtime root.
    root: Path
    #: `[harness] autonomy`, the approval posture each binding translates into its own terms.
    autonomy: Autonomy
    #: `[harness] config_dir`, the operator's bundle; `None` is no bundle.
    config_dir: Path | None
    #: The one allowlisted env every runner-spawned child is built from.
    worker_env: AllowlistedEnv
    #: Where the harness writes session transcripts; empty is the binding's own default.
    transcripts_root: str
    #: The snapshot this process published at startup, when it published one.
    bundle: BundleSnapshot | None = None
    #: How Claude Code's own exporters are wired, as the composition root planned it.
    harness_telemetry: HarnessTelemetryPlan = field(default_factory=HarnessTelemetryPlan)


class IHarnessDeclaration(Protocol[SectionT_contra]):
    """One harness binding, declared in one place."""

    @property
    def harness_id(self) -> str: ...

    @property
    def normalizer_version(self) -> str:
        """The version stamped on every transcript segment this binding's normalizer emits."""
        ...

    @property
    def bundle_layout(self) -> HarnessLayout:
        """The binding's bundle directory and entry points, without runner composition."""
        ...

    def binding(
        self,
        section: SectionT_contra,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
    ) -> HarnessBinding: ...

    def health_probe(
        self, section: SectionT_contra, shared: SharedHarnessInputs, *, spawn_root: str
    ) -> IHarnessHealthProbe: ...

    def publish_layout(self, section: SectionT_contra, *, autonomy: Autonomy, runtime_root: Path) -> HarnessLayout:
        """The bundle layout with this binding's runner wiring composed in at publish."""
        ...

    def scaffold_runtime(self, section: SectionT_contra, root: Path) -> None:
        """Write the runner-owned files ``runner init`` keeps current under ``root``; idempotent."""
        ...

    def diagnostics(self, section: SectionT_contra, shared: SharedHarnessInputs, *, spawn_root: str) -> tuple[str, ...]:
        """``harness status`` lines naming each ambient setting that defeats the runner's wiring."""
        ...

    def unbundled_status(self, section: SectionT_contra) -> tuple[str, ...]:
        """``harness status`` lines naming the runner-owned files in effect with no bundle."""
        ...

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        """``harness status`` lines naming what a worker is delivered from the published ``snapshot``."""
        ...


__all__ = ["IHarnessDeclaration", "SharedHarnessInputs"]
