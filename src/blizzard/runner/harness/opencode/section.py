"""OpenCode's config section — ``[opencode]`` and its ``models``/``effort`` alias tables.

Imports nothing from :mod:`blizzard.runner.config`: the config module reads this section."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.config_keys import BOOLEAN, OPEN_TABLE, STRING, STRING_LIST, TABLE, ConfigKey, ConfigView
from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import ConfigError, Table
from blizzard.runner.harness.identity import OPENCODE_HARNESS_ID

DEFAULT_OPENCODE_BINARY = "opencode"
# The runner-owned OpenCode permission/plugin document `init` scaffolds.
OPENCODE_WORKER_CONFIG_FILENAME = "opencode-worker-config.json"


@domain_model
@dataclass(frozen=True)
class OpenCodeSection:
    """OpenCode's parsed section."""

    #: OpenCode's own binary path, independent of Claude Code's.
    binary: str = DEFAULT_OPENCODE_BINARY
    #: `[opencode].enabled` — false leaves OpenCode unbound, unprobed, and unadvertised.
    enabled: bool = True
    #: `[opencode.models].supported` — `provider/model` names or glob patterns this binding accepts; `None` takes
    #: the adapter's built-in vocabulary, an explicit empty tuple means no native names.
    supported_models: tuple[str, ...] | None = None
    #: OpenCode's tier -> `provider/model` mapping; an unmapped tier skips this binding.
    model_aliases: tuple[tuple[str, str], ...] = ()
    #: OpenCode's effort -> `--variant` mapping; unmapped drops to `None` and logs once.
    effort_aliases: tuple[tuple[str, str], ...] = ()
    #: The runner-owned OpenCode permission/plugin document's path; `None` when none is scaffolded.
    worker_config_path: str | None = None
    #: Override for the OpenCode auth file's path; `None` takes the default.
    auth_path: str | None = None

    @property
    def harness_id(self) -> str:
        return OPENCODE_HARNESS_ID

    @property
    def configured_tiers(self) -> tuple[tuple[str, str], ...]:
        return self.model_aliases

    def autonomy_override(self) -> str | None:
        return None

    def worker_config_at(self, root: Path) -> Path:
        """The worker config's path, defaulting to the one ``runner init`` scaffolds under ``root``."""
        return Path(self.worker_config_path) if self.worker_config_path else root / OPENCODE_WORKER_CONFIG_FILENAME

    def root_toml(self) -> str:
        return ""

    def table_toml(self) -> str:
        return (
            "\n# The OpenCode binding's own configuration — fully independent of the flat\n"
            + "# Claude Code fields above, which keep their existing meaning unchanged. OpenCode\n"
            + "# ships no built-in tier mapping, so an unmapped tier makes this binding unable to\n"
            + "# satisfy a session demanding it; a multi-harness selection skips it rather than\n"
            + "# spawn it under a model it cannot provide.\n"
            + "[opencode]\n"
            + f"enabled = {'true' if self.enabled else 'false'}\n"
            + f'binary = "{self.binary}"\n'
            + f"worker_config_path = {json.dumps(self.worker_config_path or '')}\n"
            + (
                f'auth_path = "{self.auth_path}"\n'
                if self.auth_path is not None
                else '# auth_path = "/path/to/auth.json"  # defaults to the path resolved from the worker env\n'
            )
            + "\n[opencode.models]\n"
            + "# The `provider/model` names (exact, or `*` globs) this binding supports. Absent, it takes\n"
            + "# `*/*`; the list replaces it. Alias targets below bypass it.\n"
            + (
                f"supported = {json.dumps(list(self.supported_models))}\n"
                if self.supported_models is not None
                else '# supported = ["openai/*", "anthropic/claude-sonnet-4-5"]\n'
            )
            + "\n[opencode.models.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.model_aliases)
            + "\n[opencode.effort.aliases]\n"
            + "".join(f'"{alias}" = "{native}"\n' for alias, native in self.effort_aliases)
        )


@domain_model
@dataclass(frozen=True)
class OpenCodeSectionKind:
    """Reads, defaults, and scaffolds :class:`OpenCodeSection`."""

    @property
    def harness_id(self) -> str:
        return OPENCODE_HARNESS_ID

    @property
    def table(self) -> str:
        return "opencode"

    @property
    def keys(self) -> tuple[ConfigKey, ...]:
        return (
            ConfigKey("opencode", TABLE),
            ConfigKey("opencode.enabled", BOOLEAN, default=OpenCodeSection.enabled),
            ConfigKey("opencode.binary", STRING, default=DEFAULT_OPENCODE_BINARY),
            ConfigKey("opencode.auth_path", STRING),
            ConfigKey("opencode.worker_config_path", STRING),
            ConfigKey("opencode.models", TABLE),
            ConfigKey("opencode.models.supported", STRING_LIST),
            ConfigKey("opencode.models.aliases", OPEN_TABLE),
            ConfigKey("opencode.effort", TABLE),
            ConfigKey("opencode.effort.aliases", OPEN_TABLE),
        )

    def parse(self, document: ConfigView, *, root: Path, path: Path) -> OpenCodeSection:
        view = document.table(self.table)
        table = Table.of(view)
        return OpenCodeSection(
            binary=table.word("binary") or DEFAULT_OPENCODE_BINARY,
            enabled=table.boolean("enabled", True),
            auth_path=table.word("auth_path"),
            supported_models=_supported(Table.of(view.table("models"))),
            model_aliases=Table.of(view.table("models")).pairs("aliases"),
            effort_aliases=Table.of(view.table("effort")).pairs("aliases"),
            # A pre-`[opencode]` config carries no `worker_config_path`; default to the
            # same path a fresh `runner init` scaffolds rather than `None`.
            worker_config_path=table.word("worker_config_path") or str(root / OPENCODE_WORKER_CONFIG_FILENAME),
        )

    def default(self) -> OpenCodeSection:
        return OpenCodeSection()

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> OpenCodeSection:
        return OpenCodeSection(worker_config_path=str(root / OPENCODE_WORKER_CONFIG_FILENAME))


def _supported(models: Table) -> tuple[str, ...] | None:
    """The declared supported list, or ``None`` when the key is absent. Every entry must carry a ``/``: the CLI takes
    only a ``provider/model`` reference, so a name without one could be matched by the hub and then refused here."""
    if models.get("supported") is None:
        return None
    entries = models.names("supported")
    for entry in entries:
        if "/" not in entry:
            raise ConfigError(f"[opencode.models] supported entry {entry!r} must be a 'provider/model' name or pattern")
    return entries


OPENCODE_SECTION = OpenCodeSectionKind()

__all__ = [
    "DEFAULT_OPENCODE_BINARY",
    "OPENCODE_SECTION",
    "OPENCODE_WORKER_CONFIG_FILENAME",
    "OpenCodeSection",
    "OpenCodeSectionKind",
]
