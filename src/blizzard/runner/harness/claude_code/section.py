"""Claude Code's config section — ``[claude_code]``, ``[models.aliases]``/``[effort.aliases]``,
and the legacy top-level keys it predates its table with (``harness_binary``,
``harness_permission_mode``, ``worker_settings_path``, ``claude_code_credentials_path``).

Imports nothing from :mod:`blizzard.runner.config`: the config module reads this section."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import ConfigError, Table
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID

DEFAULT_HARNESS_BINARY = "claude"
# Injected per feature env, so a fresh `runner init` names the env's harness binary.
ENV_HARNESS_BINARY = "BZ_HARNESS_BINARY"
# The runner-owned worker hook file `init` scaffolds, delivering the heartbeat hook.
WORKER_SETTINGS_FILENAME = "worker-settings.json"


@domain_model
@dataclass(frozen=True)
class ClaudeCodeSection:
    """Claude Code's parsed section."""

    #: `harness_binary` or `[claude_code].binary` — mock-claude-code in tests, `claude` in prod.
    binary: str = DEFAULT_HARNESS_BINARY
    #: `[claude_code].enabled` — false leaves Claude Code unbound, unprobed, and unadvertised.
    enabled: bool = True
    #: Legacy `--permission-mode` override: `None` lets autonomy map, empty passes no flag.
    permission_mode: str | None = None
    #: The runner-owned worker hook file.
    worker_settings_path: str | None = None
    #: Override for the health probe's own credential file; `None` is its own default.
    credentials_path: str | None = None
    #: Tier aliases onto Claude Code's model names; one neither this nor the adapter maps is skipped.
    model_aliases: tuple[tuple[str, str], ...] = ()
    #: Effort alias mappings onto the `low|medium|high|max` ordinal; the well-known four need no entry.
    effort_aliases: tuple[tuple[str, str], ...] = ()

    @property
    def harness_id(self) -> str:
        return CLAUDE_CODE_HARNESS_ID

    @property
    def configured_tiers(self) -> tuple[tuple[str, str], ...]:
        return self.model_aliases

    def autonomy_override(self) -> str | None:
        return "legacy harness_permission_mode" if self.permission_mode is not None else None

    def root_toml(self) -> str:
        settings = f'"{self.worker_settings_path}"' if self.worker_settings_path else '""'
        return f"worker_settings_path = {settings}\n" + (
            f'claude_code_credentials_path = "{self.credentials_path}"\n'
            if self.credentials_path is not None
            else '# claude_code_credentials_path = "~/.claude/.credentials.json"  # this is the default\n'
        )

    def table_toml(self) -> str:
        return (
            "\n# The Claude Code binding: `enabled = false` leaves it unbound and unadvertised.\n"
            + "[claude_code]\n"
            + f"enabled = {'true' if self.enabled else 'false'}\n"
            + f'binary = "{self.binary}"\n'
            + "\n# Model and effort tier aliases — how THIS runner's harness resolves the\n"
            + "# harness-agnostic names a graph's `sessions:` declaration (or a chunk default) uses.\n"
            + "# The Claude Code adapter ships built-in defaults for the three standard tiers\n"
            + "# (blizzard:frontier/advanced/basic), so a zero-config runner needs no entry here;\n"
            + "# an entry overrides the built-in. An unmapped alias is skipped at resolution, never\n"
            + "# a spawn failure. Effort maps onto the low|medium|high|max ordinal.\n"
            + "[models.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.model_aliases)
            + "\n[effort.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.effort_aliases)
        )


@domain_model
@dataclass(frozen=True)
class ClaudeCodeSectionKind:
    """Reads, defaults, and scaffolds :class:`ClaudeCodeSection`."""

    @property
    def harness_id(self) -> str:
        return CLAUDE_CODE_HARNESS_ID

    @property
    def table(self) -> str:
        return "claude_code"

    @property
    def cli_group(self) -> tuple[str, str] | None:
        return None

    def parse(self, document: Mapping[str, Any], *, root: Path, path: Path) -> ClaudeCodeSection:
        table = Table.of(document.get(self.table))
        if "harness_binary" in document and "binary" in table.body:
            raise ConfigError("set Claude Code's binary once: 'harness_binary' or '[claude_code].binary', not both")
        permission_mode = (
            None if "harness_permission_mode" not in document else str(document["harness_permission_mode"])
        )
        if permission_mode is not None and "autonomy" in Table.of(document.get("harness")).body:
            raise ConfigError(
                f"set the approval posture once in {path}: 'harness_permission_mode' and '[harness] autonomy' "
                "are both set; keep only '[harness] autonomy'"
            )
        return ClaudeCodeSection(
            binary=str(document.get("harness_binary", table.body.get("binary", DEFAULT_HARNESS_BINARY))),
            enabled=table.boolean("enabled", True),
            permission_mode=permission_mode,
            worker_settings_path=_present(document, "worker_settings_path"),
            credentials_path=_present(document, "claude_code_credentials_path"),
            model_aliases=Table.of(document.get("models")).pairs("aliases"),
            effort_aliases=Table.of(document.get("effort")).pairs("aliases"),
        )

    def default(self) -> ClaudeCodeSection:
        return ClaudeCodeSection()

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> ClaudeCodeSection:
        # The worker hook file `init` writes alongside the config; the adapter
        # delivers it as `--settings` so a spawned worker heartbeats.
        return ClaudeCodeSection(
            binary=environ.get(ENV_HARNESS_BINARY, DEFAULT_HARNESS_BINARY),
            worker_settings_path=str(root / WORKER_SETTINGS_FILENAME),
        )


def _present(document: Mapping[str, Any], key: str) -> str | None:
    """A legacy root path key whose empty value counts as absent."""
    value = document.get(key)
    return str(value) if value else None


CLAUDE_CODE_SECTION = ClaudeCodeSectionKind()

__all__ = [
    "CLAUDE_CODE_SECTION",
    "DEFAULT_HARNESS_BINARY",
    "ENV_HARNESS_BINARY",
    "WORKER_SETTINGS_FILENAME",
    "ClaudeCodeSection",
    "ClaudeCodeSectionKind",
]
