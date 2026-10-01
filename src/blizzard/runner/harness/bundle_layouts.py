"""The harness package's public entry point for the bundle layouts each binding declares.

``config.py`` and the CLI are not composition roots for ``harness/internal/``
(``bzh:internal-visibility``); they take the declarations through this surface, which
delegates to the per-binding modules under ``harness/internal/``."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.harness.autonomy import Autonomy
from blizzard.runner.harness.bundle import (
    BundleSnapshot,
    HarnessSource,
    inspect_bundle,
    publish_bundle,
)
from blizzard.runner.harness.internal.claude_code_bundle import (
    CLAUDE_CODE_BUNDLE_LAYOUT,
    ClaudeCodeBundleDelivery,
    claude_code_bundle_layout,
)
from blizzard.runner.harness.internal.claude_code_settings_compose import resolved_permission_mode
from blizzard.runner.harness.internal.opencode_bundle import OPENCODE_BUNDLE_LAYOUT

BUNDLE_LAYOUTS = (CLAUDE_CODE_BUNDLE_LAYOUT, OPENCODE_BUNDLE_LAYOUT)


def publish_harness_bundle(
    config_dir: Path,
    runtime_root: Path,
    *,
    autonomy: Autonomy = Autonomy.Dangerous,
    permission_mode: str | None = None,
) -> BundleSnapshot:
    """Load ``config_dir`` against every binding's layout and publish it under ``runtime_root``,
    composing each binding's runner wiring in; ``autonomy`` and the legacy ``permission_mode``
    override resolve the permission mode the Claude Code composition checks against."""
    mode = resolved_permission_mode(autonomy, permission_mode)
    return publish_bundle(config_dir, runtime_root, (claude_code_bundle_layout(mode), OPENCODE_BUNDLE_LAYOUT))


def inspect_harness_bundle(config_dir: Path) -> tuple[HarnessSource, ...]:
    """Validate ``config_dir`` against every binding's layout without publishing."""
    return inspect_bundle(config_dir, BUNDLE_LAYOUTS)


def claude_code_delivery(snapshot: Path, sources: tuple[HarnessSource, ...]) -> tuple[Path, list[str]] | None:
    """The effective settings path and the flags a Claude Code worker is passed, for the
    ``snapshot`` and the bundle's ``sources``; ``None`` when the bundle has no Claude Code directory."""
    for source in sources:
        if source.dirname == CLAUDE_CODE_BUNDLE_LAYOUT.dirname:
            delivery = ClaudeCodeBundleDelivery.at(snapshot / source.dirname, source.entry_points)
            return delivery.settings, delivery.argv()
    return None
