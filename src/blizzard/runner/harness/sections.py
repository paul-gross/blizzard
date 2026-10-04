"""The config-section half of a harness declaration — what a binding reads from the TOML,
emits, scaffolds, and whether it is enabled — kept apart from construction
(:mod:`.declaration`) so :mod:`blizzard.runner.config` parses it without importing an adapter
graph that depends on the config module. :data:`HARNESS_SECTION_KINDS` declares the harness
order once, Claude Code first: the first enabled binding is the default harness."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from blizzard.foundation.roles import dto
from blizzard.runner.config_table import ConfigError
from blizzard.runner.harness.internal.claude_code_section import CLAUDE_CODE_SECTION
from blizzard.runner.harness.internal.opencode_section import OPENCODE_SECTION


class IHarnessSection(Protocol):
    """One binding's parsed config section — the facts a consumer reads off it without
    knowing which binding it is."""

    @property
    def harness_id(self) -> str: ...

    @property
    def enabled(self) -> bool:
        """False leaves the binding unbound, unprobed, and unadvertised."""
        ...

    @property
    def configured_tiers(self) -> tuple[tuple[str, str], ...]:
        """The operator's tier -> native model mapping this binding was configured with."""
        ...

    def autonomy_override(self) -> str | None:
        """The name of a legacy key overriding ``[harness] autonomy`` for this binding;
        ``None`` when the shared posture applies unaltered."""
        ...

    def root_toml(self) -> str:
        """The section's legacy top-level lines, emitted among the root keys."""
        ...

    def table_toml(self) -> str:
        """The section's own tables, emitted after the shared ones."""
        ...


class IHarnessSectionKind(Protocol):
    """How one binding's section is read, defaulted, and scaffolded, and the CLI verb group it
    mounts on ``blizzard runner`` (``None`` when it mounts none)."""

    @property
    def harness_id(self) -> str: ...

    @property
    def table(self) -> str:
        """The TOML table the section lives under."""
        ...

    @property
    def cli_group(self) -> tuple[str, str] | None:
        """The verb name and the lazy ``module:attribute`` of its click group."""
        ...

    def parse(self, document: Mapping[str, Any], *, root: Path, path: Path) -> IHarnessSection:
        """Read the section from the whole TOML ``document`` — a binding may claim legacy root
        keys — raising :class:`ConfigError` on a conflict among them."""
        ...

    def default(self) -> IHarnessSection: ...

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> IHarnessSection:
        """The section a fresh ``runner init`` at ``root`` writes, seeded from ``environ``."""
        ...


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


__all__ = [
    "HARNESS_SECTION_KINDS",
    "HarnessSections",
    "IHarnessSection",
    "IHarnessSectionKind",
    "harness_cli_groups",
    "section_of",
    "with_section",
]
