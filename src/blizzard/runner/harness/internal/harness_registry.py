"""The one neutral composition point over every coding harness the runner ships.

Iterates :data:`~blizzard.runner.harness.catalog.HARNESS_CATALOG`: each enabled declaration
builds its own binding and probe, so no adapter's concrete class escapes its declaration
(``tests/test_layering.py``) and the composition root reaches them only through this module."""

from __future__ import annotations

from concurrent.futures import Executor

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import IHarnessHealthProbe
from blizzard.runner.harness.bundle import BundleSnapshot
from blizzard.runner.harness.catalog import enabled, shared_inputs
from blizzard.runner.harness.process_launch import ProcessLauncher
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.loop.process import LinuxProcessProbe


def build_production_harness_registry(
    config: RunnerConfig, *, executor: Executor, process: LinuxProcessProbe, bundle: BundleSnapshot | None = None
) -> HarnessRegistry:
    """Build every enabled harness binding once for one graph, over one shared probe/
    launcher pair. The process graph owns the injected executor and probe for the
    lifetime of every child launch. ``bundle`` is the snapshot this process published at startup.
    Insertion follows the catalog order: the first binding is the runner's default harness."""
    launcher = ProcessLauncher(process, executor=executor)
    shared = shared_inputs(config, bundle=bundle)
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
