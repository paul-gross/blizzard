"""The runner-owned OpenCode permission/plugin document (execution spec, D7).

Written beside ``worker-settings.json`` in the runtime root, and supplied to a spawned worker
through ``OPENCODE_CONFIG``/``OPENCODE_CONFIG_CONTENT`` ("configuration_isolation"). Renders
both halves of the document: the permission denials (`question`) and the ``plugins`` naming
the heartbeat/``shell.env`` plugin — one document, never two."""

from __future__ import annotations

import json
from pathlib import Path

from blizzard.runner.harness.internal.opencode_shapes import parse_worker_config

# A headless worker has no one to answer `question`; `blizzard runner ask` replaces it, so deny rather than hang.
_UNATTENDED_DENIALS: dict[str, str] = {"question": "deny"}


def render_worker_config(*, plugins: tuple[str, ...] = ()) -> dict[str, object]:
    """The runner-owned permission/plugin document, validated against the exact shape
    :func:`opencode_shapes.parse_worker_config` accepts before anything writes it to disk —
    a malformed document must fail here, never as an unexplained provider result."""
    document: dict[str, object] = {
        "$schema": "https://opencode.ai/config.json",
        "permission": dict(_UNATTENDED_DENIALS),
        "plugin": list(plugins),
    }
    parse_worker_config(document)
    return document


def write_worker_config(path: Path, *, plugins: tuple[str, ...] = ()) -> dict[str, object]:
    """Render and persist the document at ``path`` — ``blizzard runner init`` scaffolds it
    exactly as it does ``worker-settings.json``, and idempotently: a re-run overwrites it
    with the current shape rather than leaving a stale one from an older binding."""
    document = render_worker_config(plugins=plugins)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return document


__all__ = ["render_worker_config", "write_worker_config"]
