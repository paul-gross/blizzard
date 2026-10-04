"""The one harness module naming both adapter packages, :mod:`.claude_code` and :mod:`.opencode`;
every other consumer iterates what it declares (``bzh:pluggable-seams``). :data:`HARNESS_SECTION_KINDS`
declares the harness order once, Claude Code first — the first enabled binding is the default harness —
and :data:`HARNESS_CATALOG` pairs each section kind with its binding's declaration by id."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from blizzard.foundation.roles import dto
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource, inspect_bundle, publish_bundle
from blizzard.runner.harness.claude_code.declaration import CLAUDE_CODE_DECLARATION
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_SECTION
from blizzard.runner.harness.declaration import (
    IHarnessDeclaration,
    IHarnessSection,
    IHarnessSectionKind,
    SharedHarnessInputs,
)
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.opencode.section import OPENCODE_SECTION
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.process.probe import LinuxProcessProbe

if TYPE_CHECKING:
    from blizzard.runner.config import RunnerConfig

#: The catalog order: Claude Code first, so it is the default harness whenever it is enabled.
HARNESS_SECTION_KINDS: tuple[IHarnessSectionKind, ...] = (CLAUDE_CODE_SECTION, OPENCODE_SECTION)


@dto
@dataclass(frozen=True)
class HarnessSections:
    """Every binding's parsed section, in catalog order."""

    sections: tuple[IHarnessSection, ...]

    @classmethod
    def defaults(cls) -> HarnessSections:
        return cls(tuple(kind.default() for kind in HARNESS_SECTION_KINDS))

    @classmethod
    def scaffold(cls, root: Path, environ: Mapping[str, str]) -> HarnessSections:
        return cls(tuple(kind.scaffold(root, environ) for kind in HARNESS_SECTION_KINDS))

    @classmethod
    def parse(cls, document: Mapping[str, Any], *, root: Path, path: Path) -> HarnessSections:
        """Every section read from ``document``; refuses a document enabling no binding."""
        parsed = cls(tuple(kind.parse(document, root=root, path=path) for kind in HARNESS_SECTION_KINDS))
        if not any(section.enabled for section in parsed):
            tables = " and ".join(f"'[{kind.table}].enabled'" for kind in HARNESS_SECTION_KINDS)
            quantity = "both" if len(HARNESS_SECTION_KINDS) == 2 else "all"
            raise ConfigError(f"{tables} are {quantity} false; enable at least one")
        return parsed

    def __iter__(self) -> Iterator[IHarnessSection]:
        return iter(section_of(self, kind.harness_id) for kind in HARNESS_SECTION_KINDS)


def section_of(sections: HarnessSections, harness_id: str) -> IHarnessSection:
    """The section for ``harness_id``; a binding absent from ``sections`` reads as its default
    section."""
    for section in sections.sections:
        if section.harness_id == harness_id:
            return section
    return next(kind.default() for kind in HARNESS_SECTION_KINDS if kind.harness_id == harness_id)


def with_section(sections: HarnessSections, section: IHarnessSection) -> HarnessSections:
    """``sections`` with ``section`` standing in for its binding's own."""
    return HarnessSections(
        tuple(
            section if kind.harness_id == section.harness_id else section_of(sections, kind.harness_id)
            for kind in HARNESS_SECTION_KINDS
        )
    )


def harness_cli_groups() -> dict[str, str]:
    """Every binding's mounted verb group, as ``blizzard runner``'s lazy command map takes it."""
    return dict(kind.cli_group for kind in HARNESS_SECTION_KINDS if kind.cli_group is not None)


_DECLARED: dict[str, IHarnessDeclaration[Any]] = {
    declaration.harness_id: declaration for declaration in (CLAUDE_CODE_DECLARATION, OPENCODE_DECLARATION)
}
if set(_DECLARED) != {kind.harness_id for kind in HARNESS_SECTION_KINDS}:
    raise RuntimeError("every harness section kind needs exactly one declaration, and every declaration a section")

#: Every harness declaration, in catalog order.
HARNESS_CATALOG: tuple[IHarnessDeclaration[Any], ...] = tuple(
    _DECLARED[kind.harness_id] for kind in HARNESS_SECTION_KINDS
)


def declared(sections: HarnessSections) -> Iterator[tuple[IHarnessDeclaration[Any], IHarnessSection]]:
    """Each declaration paired with its parsed section, in catalog order."""
    for declaration in HARNESS_CATALOG:
        yield declaration, section_of(sections, declaration.harness_id)


def enabled(sections: HarnessSections) -> Iterator[tuple[IHarnessDeclaration[Any], IHarnessSection]]:
    """:func:`declared`, narrowed to the bindings their sections enable."""
    return ((declaration, section) for declaration, section in declared(sections) if section.enabled)


def declared_normalizer_versions() -> tuple[str, ...]:
    """Every binding's transcript normalizer version, in catalog order."""
    return tuple(declaration.normalizer_version for declaration in HARNESS_CATALOG)


def configured_tiers(sections: HarnessSections) -> dict[str, tuple[tuple[str, str], ...]]:
    """Each binding's configured tier mapping, keyed by harness id."""
    return {declaration.harness_id: section.configured_tiers for declaration, section in declared(sections)}


def bundle_layouts() -> tuple[HarnessLayout, ...]:
    """Every binding's bundle layout, without runner composition."""
    return tuple(declaration.bundle_layout for declaration in HARNESS_CATALOG)


def shared_inputs(
    config: RunnerConfig,
    *,
    bundle: BundleSnapshot | None = None,
    harness_telemetry: HarnessTelemetryPlan | None = None,
) -> SharedHarnessInputs:
    """The runner-wide inputs ``config`` hands every binding."""
    return SharedHarnessInputs(
        root=config.root,
        autonomy=config.autonomy,
        config_dir=config.harness_config_dir,
        worker_env=config.worker_env,
        transcripts_root=config.transcripts_root,
        bundle=bundle,
        harness_telemetry=harness_telemetry or HarnessTelemetryPlan(),
    )


def build_production_harness_registry(
    config: RunnerConfig,
    *,
    executor: Executor,
    process: LinuxProcessProbe,
    bundle: BundleSnapshot | None = None,
    harness_telemetry: HarnessTelemetryPlan | None = None,
) -> HarnessRegistry:
    """Build every enabled harness binding once for one graph, over one shared probe/
    launcher pair. The process graph owns the injected executor and probe for the
    lifetime of every child launch. ``bundle`` is the snapshot this process published at startup;
    ``harness_telemetry`` is the plan the composition root derived for Claude Code's exporters.
    Insertion follows the catalog order: the first binding is the runner's default harness."""
    launcher = ProcessLauncher(process, executor=executor)
    shared = shared_inputs(config, bundle=bundle, harness_telemetry=harness_telemetry or HarnessTelemetryPlan())
    bindings: dict[str, HarnessBinding] = {
        declaration.harness_id: declaration.binding(section, shared, process=process, launcher=launcher)
        for declaration, section in enabled(config.harness_sections)
    }
    return HarnessRegistry(bindings)


def build_production_harness_health_probes(config: RunnerConfig, *, spawn_root: str) -> dict[str, IHarnessHealthProbe]:
    """Every enabled harness binding's own :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe`, symmetric with :func:`build_production_harness_registry` — the
    composition root reaches both only through this module."""
    shared = shared_inputs(config)
    return {
        declaration.harness_id: declaration.health_probe(section, shared, spawn_root=spawn_root)
        for declaration, section in enabled(config.harness_sections)
    }


BUNDLE_LAYOUTS = bundle_layouts()


def publish_harness_bundle(
    config_dir: Path,
    runtime_root: Path,
    *,
    autonomy: Autonomy = Autonomy.Dangerous,
    sections: HarnessSections | None = None,
) -> BundleSnapshot:
    """Load ``config_dir`` against every binding's layout and publish it under ``runtime_root``,
    composing each binding's runner wiring in from its own section (each defaulted when
    ``sections`` is absent) and the shared ``autonomy``."""
    parsed = sections or HarnessSections.defaults()
    layouts = tuple(
        declaration.publish_layout(section, autonomy=autonomy, runtime_root=runtime_root)
        for declaration, section in declared(parsed)
    )
    return publish_bundle(config_dir, runtime_root, layouts)


def inspect_harness_bundle(config_dir: Path) -> tuple[HarnessSource, ...]:
    """Validate ``config_dir`` against every binding's layout without publishing."""
    return inspect_bundle(config_dir, BUNDLE_LAYOUTS)


__all__ = [
    "BUNDLE_LAYOUTS",
    "HARNESS_CATALOG",
    "HARNESS_SECTION_KINDS",
    "HarnessSections",
    "build_production_harness_health_probes",
    "build_production_harness_registry",
    "bundle_layouts",
    "configured_tiers",
    "declared",
    "declared_normalizer_versions",
    "enabled",
    "harness_cli_groups",
    "inspect_harness_bundle",
    "publish_harness_bundle",
    "section_of",
    "shared_inputs",
    "with_section",
]
