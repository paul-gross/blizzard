"""OpenCode's config section — ``[opencode]`` and its ``models``/``effort`` alias tables.

Imports nothing from :mod:`blizzard.runner.config`: the config module reads this section."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from blizzard.foundation.roles import domain_model
from blizzard.runner.config_table import Table
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
    #: OpenCode's tier -> `provider/model` mapping; an unmapped tier skips this binding.
    model_aliases: tuple[tuple[str, str], ...] = ()
    #: OpenCode's effort -> `--variant` mapping; unmapped drops to `None` and logs once.
    effort_aliases: tuple[tuple[str, str], ...] = ()
    #: The runner-owned OpenCode permission/plugin document's path; `None` predates the binding.
    worker_config_path: str | None = None
    #: Override for the health probe's own auth file; `None` is its own default.
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
    def cli_group(self) -> tuple[str, str] | None:
        return ("opencode", "blizzard.runner.cli.opencode:opencode_group")

    def parse(self, document: Mapping[str, Any], *, root: Path, path: Path) -> OpenCodeSection:
        table = Table.of(document.get(self.table))
        return OpenCodeSection(
            binary=table.word("binary") or DEFAULT_OPENCODE_BINARY,
            enabled=table.boolean("enabled", True),
            auth_path=table.word("auth_path"),
            model_aliases=Table.of(table.body.get("models")).pairs("aliases"),
            effort_aliases=Table.of(table.body.get("effort")).pairs("aliases"),
            # A pre-`[opencode]` config carries no `worker_config_path`; default to the
            # same path a fresh `runner init` scaffolds rather than `None`.
            worker_config_path=table.word("worker_config_path") or str(root / OPENCODE_WORKER_CONFIG_FILENAME),
        )

    def default(self) -> OpenCodeSection:
        return OpenCodeSection()

    def scaffold(self, root: Path, environ: Mapping[str, str]) -> OpenCodeSection:
        return OpenCodeSection(worker_config_path=str(root / OPENCODE_WORKER_CONFIG_FILENAME))


OPENCODE_SECTION = OpenCodeSectionKind()

__all__ = [
    "DEFAULT_OPENCODE_BINARY",
    "OPENCODE_SECTION",
    "OPENCODE_WORKER_CONFIG_FILENAME",
    "OpenCodeSection",
    "OpenCodeSectionKind",
]
