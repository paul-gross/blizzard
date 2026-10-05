"""Test helpers over a runner config's per-harness sections: read one binding's typed section,
and build a config's sections with chosen bindings overridden."""

from __future__ import annotations

from dataclasses import replace

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.declaration import HarnessSection
from blizzard.runner.harness.opencode.section import OpenCodeSection
from blizzard.runner.harness.wiring import HarnessSections


def claude_code(config: RunnerConfig) -> ClaudeCodeSection:
    section = config.harness_sections.of(ClaudeCodeSection().harness_id)
    assert isinstance(section, ClaudeCodeSection)
    return section


def opencode(config: RunnerConfig) -> OpenCodeSection:
    section = config.harness_sections.of(OpenCodeSection().harness_id)
    assert isinstance(section, OpenCodeSection)
    return section


def sections(*overrides: HarnessSection, base: HarnessSections | None = None) -> HarnessSections:
    """``base`` (every binding's default when absent) with each of ``overrides`` standing in."""
    result = base or HarnessSections.defaults()
    for section in overrides:
        result = result.replaced(section)
    return result


def with_sections(config: RunnerConfig, *overrides: HarnessSection) -> RunnerConfig:
    """``config`` with each of ``overrides`` standing in for its binding's section."""
    return replace(config, harness_sections=sections(*overrides, base=config.harness_sections))


def with_claude_code(config: RunnerConfig, **changes: object) -> RunnerConfig:
    """``config`` with its Claude Code section's ``changes`` applied."""
    return with_sections(config, replace(claude_code(config), **changes))  # type: ignore[arg-type]


def with_opencode(config: RunnerConfig, **changes: object) -> RunnerConfig:
    """``config`` with its OpenCode section's ``changes`` applied."""
    return with_sections(config, replace(opencode(config), **changes))  # type: ignore[arg-type]
