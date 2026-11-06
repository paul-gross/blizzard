"""The one harness module naming the adapter packages, :mod:`.claude_code`, :mod:`.opencode` and :mod:`.opencode_v2`
(``bzh:pluggable-seams``). :data:`HARNESS_SECTION_KINDS` declares the harness order once; :func:`harness_families`
pairs each section kind with its declarations by id, several where the binary's version chooses, and a
:class:`BindingSelection` names the one each runner composes. A declaration's own graph loads on first use."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from blizzard.foundation.clock import IClock
from blizzard.foundation.config_keys import ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.adapter import EverySpawnRootProbe, IHarnessHealthProbe
from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.binding_selection import BindingSelection, HarnessFamilies, select_bindings
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout, HarnessSource
from blizzard.runner.harness.claude_code.section import CLAUDE_CODE_SECTION, WORKER_SETTINGS_FILENAME
from blizzard.runner.harness.declaration import (
    HarnessSection,
    IHarnessDeclaration,
    IHarnessSectionKind,
    IVersionSelectedDeclaration,
    SharedHarnessInputs,
)
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryNames, HarnessTelemetryPlan
from blizzard.runner.harness.internal.bundle_publisher import inspect_bundle, publish_bundle, published_snapshot
from blizzard.runner.harness.internal.process_launcher import ProcessLauncher
from blizzard.runner.harness.internal.version_probe import VersionProbe
from blizzard.runner.harness.offline_compatibility import ICompatibilityCorpus
from blizzard.runner.harness.opencode.plugin import PLUGIN_DIRNAME, PLUGIN_FILENAME
from blizzard.runner.harness.opencode.section import OPENCODE_SECTION, OPENCODE_WORKER_CONFIG_FILENAME
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.process.child_env import AllowlistedEnv
from blizzard.runner.process.child_processes import IChildProcesses
from blizzard.runner.process.probe import IProcessProbe

#: The catalog order: Claude Code first.
HARNESS_SECTION_KINDS: tuple[IHarnessSectionKind, ...] = (CLAUDE_CODE_SECTION, OPENCODE_SECTION)

#: Each file ``scaffold_runtime`` writes under the host root — its path there and what it holds — across the catalog.
HARNESS_RUNTIME_FILES: tuple[tuple[str, str], ...] = (
    (WORKER_SETTINGS_FILENAME, "the claude-code harness's worker settings"),
    (f"{PLUGIN_DIRNAME}/{PLUGIN_FILENAME}", "the opencode harness's heartbeat plugin"),
    (
        OPENCODE_WORKER_CONFIG_FILENAME,
        "the opencode harness's worker config, the default location that the opencode section's "
        "`worker_config_path` overrides",
    ),
)


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
    def parse(cls, document: ConfigView, *, root: Path, path: Path) -> HarnessSections:
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
    ``transcripts_root``, every binding's parsed ``sections``, and the ``runner_id`` it is served under."""

    root: Path
    autonomy: Autonomy
    config_dir: Path | None
    worker_env: AllowlistedEnv
    transcripts_root: str
    sections: HarnessSections
    #: The id the runner is served under.
    runner_id: str = ""


@cache
def harness_families() -> HarnessFamilies:
    """Each harness id's declarations in catalog order, the first chosen unless the binary's version names another —
    each adapter's graph loads on the first call."""
    from blizzard.runner.harness.claude_code.declaration import CLAUDE_CODE_DECLARATION
    from blizzard.runner.harness.opencode.declaration import OPENCODE_DECLARATION
    from blizzard.runner.harness.opencode_v2.declaration import OPENCODE_V2_DECLARATION

    families: dict[str, tuple[IHarnessDeclaration[Any], ...]] = {}
    for declaration in (CLAUDE_CODE_DECLARATION, OPENCODE_DECLARATION, OPENCODE_V2_DECLARATION):
        families[declaration.harness_id] = (*families.get(declaration.harness_id, ()), declaration)
    if set(families) != {kind.harness_id for kind in HARNESS_SECTION_KINDS}:
        raise RuntimeError("every harness section kind needs a declaration, and every declaration a section")
    if any(
        len(family) > 1 and not all(isinstance(member, IVersionSelectedDeclaration) for member in family)
        for family in families.values()
    ):
        raise RuntimeError("every declaration sharing a harness id must be chosen by its binary's version")
    return {kind.harness_id: families[kind.harness_id] for kind in HARNESS_SECTION_KINDS}


def harness_catalog() -> tuple[IHarnessDeclaration[Any], ...]:
    """Every harness declaration, in catalog order — each version-chosen declaration among them."""
    return tuple(declaration for family in harness_families().values() for declaration in family)


def select_harness_bindings(
    sections: HarnessSections, *, worker_env: AllowlistedEnv, children: IChildProcesses
) -> BindingSelection:
    """The declaration each harness id binds for ``sections``, its binary's ``--version`` run once where it chooses
    — made once when a runner is composed, and passed to everything composing it."""
    by_id = {section.harness_id: section for section in sections}
    return select_bindings(harness_families(), by_id, VersionProbe(children, worker_env.tool()))


def first_bindings() -> BindingSelection:
    """Each harness id's first declaration, for a caller composing no runner."""
    return BindingSelection.first(harness_families())


def declared(
    sections: HarnessSections, selection: BindingSelection | None = None
) -> Iterator[tuple[IHarnessDeclaration[Any], HarnessSection]]:
    """Each chosen declaration paired with its parsed section, in catalog order; with no ``selection``, each harness
    id's first."""
    chosen = selection or first_bindings()
    for harness_id in harness_families():
        yield chosen.of(harness_id), sections.of(harness_id)


def enabled(
    sections: HarnessSections, selection: BindingSelection | None = None
) -> Iterator[tuple[IHarnessDeclaration[Any], HarnessSection]]:
    """:func:`declared`, narrowed to the bindings their sections enable."""
    return ((declaration, section) for declaration, section in declared(sections, selection) if section.enabled)


def declared_normalizer_versions() -> tuple[str, ...]:
    """Every binding's transcript normalizer version, in catalog order."""
    return tuple(declaration.normalizer_version for declaration in harness_catalog())


def configured_tiers(sections: HarnessSections) -> dict[str, tuple[tuple[str, str], ...]]:
    """Each binding's configured tier mapping, keyed by harness id."""
    return {declaration.harness_id: section.configured_tiers for declaration, section in declared(sections)}


def bundle_layouts(selection: BindingSelection | None = None) -> tuple[HarnessLayout, ...]:
    """Each harness id's bundle layout, without runner composition — its first declaration's with no ``selection``."""
    chosen = selection or first_bindings()
    return tuple(chosen.of(harness_id).bundle_layout for harness_id in harness_families())


def declared_telemetry_names() -> tuple[HarnessTelemetryNames, ...]:
    """The names every binding's own telemetry arrives under, in catalog order — enabled or not.

    Catalog-wide on purpose: the telemetry receiver admitting these names is one host-level singleton,
    built before any runner and serving every runner's leases, so it cannot narrow to one runner's
    enabled bindings. :func:`combined_telemetry_plan` is the per-runner, enabled-only status read."""
    named = (declaration.telemetry_names for declaration in harness_catalog())
    return tuple(dict.fromkeys(names for names in named if names is not None))


def combined_telemetry_plan(
    settings: HarnessSettings,
    *,
    selection: BindingSelection | None = None,
    bundle: BundleSnapshot | None = None,
    harness_telemetry_enabled: bool = False,
    runner_environ: Mapping[str, str] | None = None,
) -> HarnessTelemetryPlan:
    """What the enabled bindings together do with each telemetry signal, the most engaged outcome per signal.

    Per runner and enabled bindings only, so it reads off for a runner with no enabled binding that ships
    telemetry — even though the host's receiver still admits that binding's names
    (:func:`declared_telemetry_names`, catalog-wide). The two answer different questions; neither is wrong."""
    shared = shared_inputs(
        settings,
        bundle=bundle,
        harness_telemetry_enabled=harness_telemetry_enabled,
        runner_environ=runner_environ,
    )
    return HarnessTelemetryPlan.combine(
        declaration.telemetry_plan(section, shared) for declaration, section in enabled(settings.sections, selection)
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
        runner_id=settings.runner_id,
    )


def build_production_harness_registry(
    settings: HarnessSettings,
    *,
    selection: BindingSelection | None = None,
    executor: Executor,
    process: IProcessProbe,
    children: IChildProcesses,
    clock: IClock,
    bundle: BundleSnapshot | None = None,
    harness_telemetry_enabled: bool = False,
    runner_environ: Mapping[str, str] | None = None,
) -> HarnessRegistry:
    """Build every enabled harness binding once for one graph, over one shared probe/
    launcher pair. The process graph owns the injected executor and probe for the
    lifetime of every child launch. ``bundle`` is the snapshot this process published at startup;
    ``harness_telemetry_enabled`` and ``runner_environ`` are what each binding derives its own telemetry plan from.
    Insertion follows the catalog order."""
    launcher = ProcessLauncher(process, executor=executor, children=children)
    shared = shared_inputs(
        settings,
        bundle=bundle,
        harness_telemetry_enabled=harness_telemetry_enabled,
        runner_environ=runner_environ,
    )
    bindings: dict[str, HarnessBinding] = {
        declaration.harness_id: declaration.binding(
            section, shared, process=process, launcher=launcher, children=children, clock=clock
        )
        for declaration, section in enabled(settings.sections, selection)
    }
    return HarnessRegistry(bindings)


def build_production_harness_health_probes(
    settings: HarnessSettings,
    *,
    selection: BindingSelection | None = None,
    spawn_roots: Sequence[str],
    corpus: ICompatibilityCorpus,
    children: IChildProcesses,
) -> dict[str, IHarnessHealthProbe]:
    """Every enabled harness binding's own :class:`~blizzard.runner.harness.adapter.
    IHarnessHealthProbe`, symmetric with :func:`build_production_harness_registry` — checking ambient
    settings once per distinct spawn root in ``spawn_roots``, and admitting versions against ``corpus``."""
    shared = shared_inputs(settings)
    roots = tuple(dict.fromkeys(spawn_roots)) or ("",)
    probes = {
        declaration.harness_id: tuple(
            declaration.health_probe(section, shared, spawn_root=root, corpus=corpus, children=children)
            for root in (roots if declaration.health_varies_by_spawn_root else roots[:1])
        )
        for declaration, section in enabled(settings.sections, selection)
    }
    return {harness_id: each[0] if len(each) == 1 else EverySpawnRootProbe(each) for harness_id, each in probes.items()}


def publish_harness_bundle(
    config_dir: Path,
    runtime_root: Path,
    *,
    autonomy: Autonomy = Autonomy.Dangerous,
    sections: HarnessSections | None = None,
    selection: BindingSelection | None = None,
) -> BundleSnapshot:
    """Load ``config_dir`` against every binding's layout and publish it under ``runtime_root``,
    composing each binding's runner wiring in from its own section (each defaulted when
    ``sections`` is absent) and the shared ``autonomy``."""
    parsed = sections or HarnessSections.defaults()
    layouts = tuple(
        declaration.publish_layout(section, autonomy=autonomy, runtime_root=runtime_root)
        for declaration, section in declared(parsed, selection)
    )
    return publish_bundle(config_dir, runtime_root, layouts)


def inspect_harness_bundle(config_dir: Path, selection: BindingSelection | None = None) -> tuple[HarnessSource, ...]:
    """Validate ``config_dir`` against each chosen binding's layout without publishing."""
    return inspect_bundle(config_dir, bundle_layouts(selection))


def published_harness_snapshot(runtime_root: Path) -> Path | None:
    """The snapshot ``runtime_root``'s ``current`` resolves to, or ``None`` when nothing is published."""
    return published_snapshot(runtime_root)


__all__ = [
    "HARNESS_RUNTIME_FILES",
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
    "first_bindings",
    "harness_catalog",
    "harness_families",
    "inspect_harness_bundle",
    "publish_harness_bundle",
    "published_harness_snapshot",
    "select_harness_bindings",
    "shared_inputs",
]
