"""Test helpers over a runner config's per-harness sections: read one binding's typed section,
and build a config's sections with chosen bindings overridden."""

from __future__ import annotations

from dataclasses import replace

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.internal.claude_code_section import ClaudeCodeSection
from blizzard.runner.harness.internal.opencode_section import OpenCodeSection
from blizzard.runner.harness.sections import HarnessSections, IHarnessSection, section_of, with_section


def claude_code(config: RunnerConfig) -> ClaudeCodeSection:
    section = section_of(config.harness_sections, ClaudeCodeSection().harness_id)
    assert isinstance(section, ClaudeCodeSection)
    return section


def opencode(config: RunnerConfig) -> OpenCodeSection:
    section = section_of(config.harness_sections, OpenCodeSection().harness_id)
    assert isinstance(section, OpenCodeSection)
    return section


def sections(*overrides: IHarnessSection, base: HarnessSections | None = None) -> HarnessSections:
    """``base`` (every binding's default when absent) with each of ``overrides`` standing in."""
    result = base or HarnessSections.defaults()
    for section in overrides:
        result = with_section(result, section)
    return result


def with_sections(config: RunnerConfig, *overrides: IHarnessSection) -> RunnerConfig:
    """``config`` with each of ``overrides`` standing in for its binding's section."""
    return replace(config, harness_sections=sections(*overrides, base=config.harness_sections))


def with_claude_code(config: RunnerConfig, **changes: object) -> RunnerConfig:
    """``config`` with its Claude Code section's ``changes`` applied."""
    return with_sections(config, replace(claude_code(config), **changes))  # type: ignore[arg-type]


def with_opencode(config: RunnerConfig, **changes: object) -> RunnerConfig:
    """``config`` with its OpenCode section's ``changes`` applied."""
    return with_sections(config, replace(opencode(config), **changes))  # type: ignore[arg-type]
