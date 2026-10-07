"""The one harness module naming both adapter packages, :mod:`.claude_code` and :mod:`.opencode`
(``bzh:pluggable-seams``). :data:`HARNESS_SECTION_KINDS` declares the harness order once, Claude Code
first (the first enabled binding is the default), and :func:`harness_catalog` pairs each section kind
with its binding's declaration by id. Importing it loads only each adapter's ``section`` module; a
declaration's own graph loads on first use."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from concurrent.futures import Executor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_SECTION
from blizzard.runner.harness.declaration import (
    HarnessSection,
    IHarnessDeclaration,
    IHarnessSectionKind,
    SharedHarnessInputs,
)
from blizzard.runner.harness.env_allowlist import AllowlistedEnv
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.internal.bundle_publisher import inspect_bundle, publish_bundle, published_snapshot
from blizzard.runner.harness.opencode.section import OPENCODE_SECTION
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.process.probe import IProcessProbe

#: The catalog order: Claude Code first, so it is the default harness whenever it is enabled.
HARNESS_SECTION_KINDS: tuple[IHarnessSectionKind, ...] = (CLAUDE_CODE_SECTION, OPENCODE_SECTION)


@domain_model
@dataclass(frozen=True)
class HarnessSections:
    """Every binding's parsed section, in catalog order."""

    sections: tuple[HarnessSection, ...]

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

    def of(self, harness_id: str) -> HarnessSection:
        """The section for ``harness_id``; a binding absent here reads as its default section."""
        for section in self.sections:
            if section.harness_id == harness_id:
                return section
        return next(kind.default() for kind in HARNESS_SECTION_KINDS if kind.harness_id == harness_id)

    def replaced(self, section: HarnessSection) -> HarnessSections:
        """These sections with ``section`` standing in for its binding's own."""
        return HarnessSections(
            tuple(
                section if kind.harness_id == section.harness_id else self.of(kind.harness_id)
                for kind in HARNESS_SECTION_KINDS
            )
        )

    def __iter__(self) -> Iterator[HarnessSection]:
        return iter(self.of(kind.harness_id) for kind in HARNESS_SECTION_KINDS)


@domain_model
@dataclass(frozen=True)
class HarnessSettings:
    """The runner-wide values every binding is built from: the runner ``root``, the worker
    ``autonomy``, the operator bundle's ``config_dir``, the allowlisted ``worker_env``, the
    ``transcripts_root``, and every binding's parsed ``sections``."""

    root: Path
    autonomy: Autonomy
    config_dir: Path | None
    worker_env: AllowlistedEnv
    transcripts_root: str
    sections: HarnessSections


@cache
def harness_catalog() -> tuple[IHarnessDeclaration[Any], ...]:
    """Every harness declaration, in catalog order — each adapter's graph loads on the first call."""
    from blizzard.runner.harness.claude_code.declaration import CLAUDE_CODE_DECLARATION
    from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION

    declarations = {
        declaration.harness_id: declaration for declaration in (CLAUDE_CODE_DECLARATION, OPENCODE_DECLARATION)
    }
    if set(declarations) != {kind.harness_id for kind in HARNESS_SECTION_KINDS}:
        raise RuntimeError("every harness section kind needs exactly one declaration, and every declaration a section")
    return tuple(declarations[kind.harness_id] for kind in HARNESS_SECTION_KINDS)


def declared(sections: HarnessSections) -> Iterator[tuple[IHarnessDeclaration[Any], HarnessSection]]:
    """Each declaration paired with its parsed section, in catalog order."""
    for declaration in harness_catalog():
        yield declaration, sections.of(declaration.harness_id)


def enabled(sections: HarnessSections) -> Iterator[tuple[IHarnessDeclaration[Any], HarnessSection]]:
    """:func:`declared`, narrowed to the bindings their sections enable."""
    return ((declaration, section) for declaration, section in declared(sections) if section.enabled)


def declared_normalizer_versions() -> tuple[str, ...]:
    """Every binding's transcript normalizer version, in catalog order."""
    return tuple(declaration.normalizer_version for declaration in harness_catalog())


def configured_tiers(sections: HarnessSections) -> dict[str, tuple[tuple[str, str], ...]]:
    """Each binding's configured tier mapping, keyed by harness id."""
    return {declaration.harness_id: section.configured_tiers for declaration, section in declared(sections)}


def bundle_layouts() -> tuple[HarnessLayout, ...]:
    """Every binding's bundle layout, without runner composition."""
    return tuple(declaration.bundle_layout for declaration in harness_catalog())


def declared_telemetry_names() -> tuple[HarnessTelemetryNames, ...]:
    """The names every binding's own telemetry arrives under, in catalog order — enabled or not."""
    return tuple(names for declaration in harness_catalog() if (names := declaration.telemetry_names) is not None)


def combined_telemetry_plan(
    settings: HarnessSettings,
    *,
    bundle: BundleSnapshot | None = None,
    harness_telemetry_enabled: bool = False,
    runner_environ: Mapping[str, str] | None = None,
) -> HarnessTelemetryPlan:
    """What the enabled bindings together do with each telemetry signal, the most engaged outcome per signal."""
    shared = shared_inputs(
        settings,
        bundle=bundle,
        harness_telemetry_enabled=harness_telemetry_enabled,
        runner_environ=runner_environ,
    )
    return HarnessTelemetryPlan.combine(
        declaration.telemetry_plan(section, shared) for declaration, section in enabled(settings.sections)
    )


def shared_inputs(
    settings: HarnessSettings,
    *,
    bundle: BundleSnapshot | None = None,
    harness_telemetry_enabled: bool = False,
    runner_environ: Mapping[str, str] | None = None,
) -> SharedHarnessInputs:
    """The runner-wide inputs ``settings`` hands every binding."""
    return SharedHarnessInputs(
        root=settings.root,
        autonomy=settings.autonomy,
        config_dir=settings.config_dir,
        worker_env=settings.worker_env,
        transcripts_root=settings.transcripts_root,
        bundle=bundle,
        harness_telemetry_enabled=harness_telemetry_enabled,
        runner_environ=runner_environ or {},
    )


def build_production_harness_registry(
    settings: HarnessSettings,
    *,
    executor: Executor,
    process: IProcessProbe,
    bundle: BundleSnapshot | None = None,
    harness_telemetry_enabled: bool = False,
    runner_environ: Mapping[str, str] | None = None,
) -> HarnessRegistry:
    """Build every enabled harness binding once for one graph, over one shared probe/
    launcher pair. The process graph owns the injected executor and probe for the
    lifetime of every child launch. ``bundle`` is the snapshot this process published at startup;
    ``harness_telemetry_enabled`` and ``runner_environ`` are what each binding derives its own telemetry plan from.
    Insertion follows the catalog order: the first binding is the runner's default harness."""
    launcher = ProcessLauncher(process, executor=executor)
    shared = shared_inputs(
        settings,
        bundle=bundle,
        harness_telemetry_enabled=harness_telemetry_enabled,
        runner_environ=runner_environ,
    )
    bindings: dict[str, HarnessBinding] = {
        declaration.harness_id: declaration.binding(section, shared, process=process, launcher=launcher)
        for declaration, section in enabled(settings.sections)
    }
    return HarnessRegistry(bindings)


def build_production_harness_health_probes(
    settings: HarnessSettings, *, spawn_root: str
) -> dict[str, IHarnessHealthProbe]:
    """Every enabled harness binding's own :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe`, symmetric with :func:`build_production_harness_registry`."""
    shared = shared_inputs(settings)
    return {
        declaration.harness_id: declaration.health_probe(section, shared, spawn_root=spawn_root)
        for declaration, section in enabled(settings.sections)
    }


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
    return inspect_bundle(config_dir, bundle_layouts())


def published_harness_snapshot(runtime_root: Path) -> Path | None:
    """The snapshot ``runtime_root``'s ``current`` resolves to, or ``None`` when nothing is published."""
    return published_snapshot(runtime_root)


__all__ = [
    "HARNESS_SECTION_KINDS",
    "HarnessSections",
    "HarnessSettings",
    "build_production_harness_health_probes",
    "build_production_harness_registry",
    "bundle_layouts",
    "combined_telemetry_plan",
    "configured_tiers",
    "declared",
    "declared_normalizer_versions",
    "declared_telemetry_names",
    "enabled",
    "harness_catalog",
    "inspect_harness_bundle",
    "publish_harness_bundle",
    "published_harness_snapshot",
    "shared_inputs",
]
