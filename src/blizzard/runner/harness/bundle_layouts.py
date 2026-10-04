"""The harness package's public entry point for the bundle layouts each binding declares.

The CLI is not a composition root for ``harness/internal/`` (``bzh:internal-visibility``); it
takes every binding's layout through this surface, which iterates the harness catalog."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessSource, inspect_bundle, publish_bundle
from blizzard.runner.harness.catalog import bundle_layouts, declared
from blizzard.runner.harness.sections import HarnessSections

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
