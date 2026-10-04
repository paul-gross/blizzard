"""The ordered catalog of every harness binding the runner ships — the owner-side surface a
consumer iterates instead of naming a binding (``bzh:internal-visibility``).

The order is :data:`blizzard.runner.harness.sections.HARNESS_SECTION_KINDS`'s — declared
once, Claude Code first — and each declaration is paired with its parsed section by id."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.bundle import BundleSnapshot, HarnessLayout
from blizzard.runner.harness.declaration import IHarnessDeclaration, SharedHarnessInputs
from blizzard.runner.harness.harness_telemetry import HarnessTelemetryPlan
from blizzard.runner.harness.internal.claude_code_declaration import CLAUDE_CODE_DECLARATION
from blizzard.runner.harness.internal.opencode_declaration import OPENCODE_DECLARATION
from blizzard.runner.harness.sections import HARNESS_SECTION_KINDS, HarnessSections, IHarnessSection, section_of

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


__all__ = [
    "HARNESS_CATALOG",
    "bundle_layouts",
    "configured_tiers",
    "declared",
    "declared_normalizer_versions",
    "enabled",
    "shared_inputs",
]
