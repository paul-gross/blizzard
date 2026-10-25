"""A harness declaration in two halves: its config section — what a binding reads from the TOML,
emits, scaffolds, and whether it is enabled (:class:`IHarnessSectionKind`, :class:`HarnessSection`) —
and its construction, what a binding builds or reports from that section plus the runner-wide inputs
every binding shares (:class:`IHarnessDeclaration`). A consumer iterates
:func:`~blizzard.runner.harness.wiring.harness_catalog` and never names a binding; adding a harness is
one adapter package under ``harness/`` declaring both halves, and one entry in :mod:`.wiring`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, TypeVar

from blizzard.foundation.config_keys import ConfigKey, ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus
from blizzard.runner.harness.process_launch import IProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.probe import IProcessProbe


class HarnessSection(Protocol):
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
    """How one binding's section is read, defaulted, and scaffolded."""

    @property
    def harness_id(self) -> str: ...

    @property
    def table(self) -> str:
        """The TOML table the section lives under."""
        ...

    @property
    def keys(self) -> tuple[ConfigKey, ...]:
        """Every key the section reads, relative to a ``[[runner]]`` — its own table's and any legacy root keys."""
        ...

    def parse(self, document: ConfigView, *, root: Path, path: Path) -> HarnessSection:
        """Read the section from the whole ``[[runner]]`` ``document`` — a binding may claim legacy root
        keys — raising :class:`ConfigError` on a conflict among them."""
        ...

    def default(self) -> HarnessSection: ...

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> HarnessSection:
        """The section a fresh ``runner init`` at ``root`` writes, seeded from ``environ``."""
        ...


SectionT_contra = TypeVar("SectionT_contra", bound=HarnessSection, contravariant=True)


@domain_model
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
    #: `[tracing] harness_telemetry` together with platform tracing — whether a binding captures its telemetry.
    harness_telemetry_enabled: bool = False
    #: The runner's own environ, where a binding finds whether it has a destination to capture a signal to.
    runner_environ: Mapping[str, str] = field(default_factory=dict[str, str])


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

    @property
    def telemetry_names(self) -> HarnessTelemetryNames | None:
        """What the binding's own telemetry arrives under; ``None`` for a binding that exports none."""
        ...

    def telemetry_plan(self, section: SectionT_contra, shared: SharedHarnessInputs) -> HarnessTelemetryPlan:
        """What the binding does with each telemetry signal's exporter under ``section`` and ``shared``."""
        ...

    def binding(
        self,
        section: SectionT_contra,
        shared: SharedHarnessInputs,
        *,
        process: IProcessProbe,
        launcher: IProcessLauncher,
        children: IChildProcesses,
    ) -> HarnessBinding:
        """The binding's adapter and transcript source; ``launcher`` starts its worker, judge and resume children,
        ``children`` every other child it runs (version probes, exports, resolvers)."""
        ...

    def health_probe(
        self,
        section: SectionT_contra,
        shared: SharedHarnessInputs,
        *,
        spawn_root: str,
        corpus: ICompatibilityCorpus,
        children: IChildProcesses,
    ) -> IHarnessHealthProbe: ...

    def publish_layout(self, section: SectionT_contra, *, autonomy: Autonomy, runtime_root: Path) -> HarnessLayout:
        """The bundle layout with this binding's runner wiring composed in at publish."""
        ...

    def scaffold_runtime(self, section: SectionT_contra, root: Path) -> None:
        """Write the runner-owned files ``runner init`` keeps current under ``root``; idempotent."""
        ...

    def diagnostics(self, section: SectionT_contra, shared: SharedHarnessInputs, *, spawn_root: str) -> tuple[str, ...]:
        """Lines naming each ambient setting that defeats the runner's wiring."""
        ...

    def unbundled_status(self, section: SectionT_contra) -> tuple[str, ...]:
        """Lines naming the runner-owned files in effect with no bundle."""
        ...

    def snapshot_status(self, snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[str, ...]:
        """Lines naming what a worker is delivered from the published ``snapshot``."""
        ...


__all__ = ["HarnessSection", "IHarnessDeclaration", "IHarnessSectionKind", "SharedHarnessInputs"]
