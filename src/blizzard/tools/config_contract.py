"""Build a daemon's config contract document from its registry.

The document lists every key the daemon reads from its toml file — path, type, whether it is required, its default, and
whether it is deprecated and what replaces it — and every durable path under its runtime root, each sorted by path so a
drift check over the committed output is stable. A path lists the verbs that write it and its enforced mode as a
four-digit octal string; the ``env`` section renders the env file that holds the tokens, or is ``None`` when the daemon
declares none."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.config_keys import ConfigRegistry, EnvFile


def document_from_registry(registry: ConfigRegistry) -> dict[str, Any]:
    return {
        "daemon": registry.name,
        "keys": [
            {
                "path": key.path,
                "type": key.type,
                "required": key.required,
                "default": key.default,
                "deprecated": key.deprecated,
                "replaced_by": key.replaced_by,
            }
            for key in sorted(registry.keys, key=lambda key: key.path)
        ],
        "paths": [
            {
                "path": entry.path,
                "holds": entry.holds,
                "written_by": sorted(entry.written_by),
                "mode": _octal(entry.mode),
            }
            for entry in sorted(registry.paths, key=lambda e: e.path)
        ],
        "env": _env(registry.env),
    }


def _octal(mode: int | None) -> str | None:
    return None if mode is None else f"{mode:04o}"


def _env(env: EnvFile | None) -> dict[str, Any] | None:
    if env is None:
        return None
    return {
        "file": env.file,
        "mode": _octal(env.mode),
        "atomic": env.atomic,
        "variables": [
            {
                "name": variable.name,
                "holds": variable.holds,
                "name_rule": None
                if variable.name_rule is None
                else {
                    "prefix": variable.name_rule.prefix,
                    "upper_case": variable.name_rule.upper_case,
                    "replace_pattern": variable.name_rule.replace_pattern,
                    "replacement": variable.name_rule.replacement,
                },
            }
            for variable in sorted(env.variables, key=lambda v: v.name)
        ],
    }
