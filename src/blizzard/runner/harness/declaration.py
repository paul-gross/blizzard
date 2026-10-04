"""A harness declaration in two halves: its config section — what a binding reads from the TOML,
emits, scaffolds, and whether it is enabled (:class:`IHarnessSectionKind`, :class:`IHarnessSection`) —
and its construction, what a binding builds or reports from that section plus the runner-wide inputs
every binding shares (:class:`IHarnessDeclaration`). A consumer iterates
:data:`~blizzard.runner.harness.wiring.HARNESS_CATALOG` and never names a binding; adding a harness is
one adapter package under ``harness/`` declaring both halves, and one entry in :mod:`.wiring`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TypeVar

from blizzard.foundation.roles import dto
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.process.probe import IProcessProbe


class IHarnessSection(Protocol):
    """One binding's parsed config section — the facts a consumer reads off it without
    knowing which binding it is."""

    @property
    def harness_id(self) -> str: ...

    @property
    def enabled(self) -> bool:
        """False leaves the binding unbound, unprobed, and unadvertised."""
        ...

    @property
    def configured_tiers(self) -> tuple[tuple[str, str], ...]:
        """The operator's tier -> native model mapping this binding was configured with."""
        ...

    def autonomy_override(self) -> str | None:
        """The name of a legacy key overriding ``[harness] autonomy`` for this binding;
        ``None`` when the shared posture applies unaltered."""
        ...

    def root_toml(self) -> str:
        """The section's legacy top-level lines, emitted among the root keys."""
        ...

    def table_toml(self) -> str:
        """The section's own tables, emitted after the shared ones."""
        ...


class IHarnessSectionKind(Protocol):
    """How one binding's section is read, defaulted, and scaffolded, and the CLI verb group it
    mounts on ``blizzard runner`` (``None`` when it mounts none)."""

    @property
    def harness_id(self) -> str: ...

    @property
    def table(self) -> str:
        """The TOML table the section lives under."""
        ...

    @property
    def cli_group(self) -> tuple[str, str] | None:
        """The verb name and the lazy ``module:attribute`` of its click group."""
        ...

    def parse(self, document: Mapping[str, Any], *, root: Path, path: Path) -> IHarnessSection:
        """Read the section from the whole TOML ``document`` — a binding may claim legacy root
        keys — raising :class:`ConfigError` on a conflict among them."""
        ...

    def default(self) -> IHarnessSection: ...

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> IHarnessSection:
        """The section a fresh ``runner init`` at ``root`` writes, seeded from ``environ``."""
        ...


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


__all__ = ["IHarnessDeclaration", "IHarnessSection", "IHarnessSectionKind", "SharedHarnessInputs"]
