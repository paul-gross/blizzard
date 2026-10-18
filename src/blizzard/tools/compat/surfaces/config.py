"""The configuration surface: the keys and durable paths a daemon declares in its contract document.

``flags`` is one open-table entry; the keys inside it are flag names, never configuration keys."""

from __future__ import annotations

from typing import Any

from blizzard.tools.compat.violation import Violation

SURFACE = "config"
_OPEN_TABLE_KEYS = "flags."


def _keys(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["path"]: k for k in document.get("keys", []) if not k["path"].startswith(_OPEN_TABLE_KEYS)}


def classify_config(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A key or path removed, a key whose type changed, and a key that became required."""
    findings: list[Violation] = []
    base_keys, head_keys = _keys(base), _keys(head)
    for path, before in base_keys.items():
        deprecated = bool(before.get("deprecated"))
        after = head_keys.get(path)
        if after is None:
            findings.append(Violation(SURFACE, path, "configuration key removed", deprecated))
            continue
        if before.get("type") != after.get("type"):
            reason = f"configuration key type changed from {before.get('type')} to {after.get('type')}"
            findings.append(Violation(SURFACE, path, reason, deprecated))
        if after.get("required") and not before.get("required"):
            findings.append(Violation(SURFACE, path, "configuration key became required", deprecated))
    for path, after in head_keys.items():
        if path not in base_keys and after.get("required"):
            findings.append(Violation(SURFACE, path, "configuration key became required"))
    head_paths = {p["path"] for p in head.get("paths", [])}
    for entry in base.get("paths", []):
        if entry["path"] not in head_paths:
            findings.append(Violation(SURFACE, entry["path"], "durable path removed", bool(entry.get("deprecated"))))
    return findings
