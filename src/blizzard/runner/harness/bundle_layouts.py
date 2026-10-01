"""The harness package's public entry point for the bundle layouts each binding declares.

``config.py`` and the CLI are not composition roots for ``harness/internal/``
(``bzh:internal-visibility``); they take the declarations through this surface, which
delegates to the per-binding modules under ``harness/internal/``."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.bundle import (
    BundleSnapshot,
    HarnessSource,
    inspect_bundle,
    publish_bundle,
)
from blizzard.runner.harness.internal.claude_code_bundle import CLAUDE_CODE_BUNDLE_LAYOUT
from blizzard.runner.harness.internal.opencode_bundle import OPENCODE_BUNDLE_LAYOUT

BUNDLE_LAYOUTS = (CLAUDE_CODE_BUNDLE_LAYOUT, OPENCODE_BUNDLE_LAYOUT)


def publish_harness_bundle(config_dir: Path, runtime_root: Path) -> BundleSnapshot:
    """Load ``config_dir`` against every binding's layout and publish it under ``runtime_root``."""
    return publish_bundle(config_dir, runtime_root, BUNDLE_LAYOUTS)


def inspect_harness_bundle(config_dir: Path) -> tuple[HarnessSource, ...]:
    """Validate ``config_dir`` against every binding's layout without publishing."""
    return inspect_bundle(config_dir, BUNDLE_LAYOUTS)
