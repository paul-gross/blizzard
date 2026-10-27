"""Claude Code's config section — ``[claude_code]``, ``[models.aliases]``/``[effort.aliases]``,
and the legacy top-level keys it predates its table with (``harness_binary``,
``harness_permission_mode``, ``worker_settings_path``, ``claude_code_credentials_path``).

Imports nothing from :mod:`blizzard.runner.config`: the config module reads this section."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.config_keys import BOOLEAN, OPEN_TABLE, STRING, STRING_LIST, TABLE, ConfigKey, ConfigView
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
    #: `[models].supported` — the native names or glob patterns this binding accepts; `None` takes the adapter's
    #: built-in vocabulary, an explicit empty tuple means no native names.
    supported_models: tuple[str, ...] | None = None
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
            + "[models]\n"
            + "# The native model names (exact, or `*` globs) this binding supports. Absent, it takes the\n"
            + "# built-in vocabulary; the list replaces it. Alias targets above bypass it.\n"
            + (
                f"supported = {json.dumps(list(self.supported_models))}\n"
                if self.supported_models is not None
                else '# supported = ["fable", "opus", "sonnet", "haiku", "claude-*"]  # the default\n'
            )
            + "\n[models.aliases]\n"
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
    def keys(self) -> tuple[ConfigKey, ...]:
        return (
            ConfigKey("claude_code", TABLE),
            ConfigKey("claude_code.enabled", BOOLEAN, default=ClaudeCodeSection.enabled),
            ConfigKey("claude_code.binary", STRING, default=DEFAULT_HARNESS_BINARY),
            ConfigKey("models", TABLE),
            ConfigKey("models.supported", STRING_LIST),
            ConfigKey("models.aliases", OPEN_TABLE),
            ConfigKey("effort", TABLE),
            ConfigKey("effort.aliases", OPEN_TABLE),
            ConfigKey("harness_binary", STRING, deprecated=True, replaced_by="claude_code.binary"),
            ConfigKey("harness_permission_mode", STRING, deprecated=True, replaced_by="harness.autonomy"),
            ConfigKey("worker_settings_path", STRING, deprecated=True),
            ConfigKey("claude_code_credentials_path", STRING, deprecated=True),
        )

    def parse(self, document: ConfigView, *, root: Path, path: Path) -> ClaudeCodeSection:
        table = Table.of(document.table(self.table))
        if "harness_binary" in document and "binary" in table.view:
            raise ConfigError("set Claude Code's binary once: 'harness_binary' or '[claude_code].binary', not both")
        permission_mode = (
            None if "harness_permission_mode" not in document else str(document.get("harness_permission_mode"))
        )
        if permission_mode is not None and "autonomy" in document.table("harness"):
            raise ConfigError(
                f"set the approval posture once in {path}: 'harness_permission_mode' and '[harness] autonomy' "
                "are both set; keep only '[harness] autonomy'"
            )
        return ClaudeCodeSection(
            binary=str(document.get("harness_binary", table.view.get("binary", DEFAULT_HARNESS_BINARY))),
            enabled=table.boolean("enabled", True),
            permission_mode=permission_mode,
            worker_settings_path=_present(document, "worker_settings_path"),
            credentials_path=_present(document, "claude_code_credentials_path"),
            supported_models=_supported(Table.of(document.table("models"))),
            model_aliases=Table.of(document.table("models")).pairs("aliases"),
            effort_aliases=Table.of(document.table("effort")).pairs("aliases"),
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


def _supported(models: Table) -> tuple[str, ...] | None:
    """The declared supported list, or ``None`` when the key is absent."""
    return None if models.get("supported") is None else models.names("supported")


def _present(document: ConfigView, key: str) -> str | None:
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
