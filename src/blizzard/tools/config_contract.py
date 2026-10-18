"""Build a daemon's config contract document from its registry.

The document lists every key the daemon reads from its toml file — path, type, whether it is required, its default, and
whether it is deprecated and what replaces it — and every durable path under its runtime root, each sorted by path so a
drift check over the committed output is stable."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.config_keys import ConfigRegistry


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
        "paths": [{"path": entry.path, "holds": entry.holds} for entry in sorted(registry.paths, key=lambda e: e.path)],
    }
