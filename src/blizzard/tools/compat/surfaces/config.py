"""The configuration surface: the keys and durable paths a daemon declares in its contract document.

``flags`` is one open-table entry; the keys inside it are flag names, never configuration keys."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.config_keys import LIFETIME_ORDER, Lifetime
from blizzard.tools.compat.violation import Violation

SURFACE = "config"
_OPEN_TABLE_KEYS = "flags."


def _keys(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k["path"]: k for k in document.get("keys", []) if not k["path"].startswith(_OPEN_TABLE_KEYS)}


def _rank(lifetime: object) -> int | None:
    """The lifetime's place in the order, or None for a value outside it."""
    try:
        return LIFETIME_ORDER.index(Lifetime(lifetime))
    except ValueError:
        return None


def classify_config(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A key or path removed, a key whose type changed, a key that became required, and a key whose lifetime moved
    later — a later lifetime narrows what an operator changes without a restart; a key lacking one on either side is
    not compared."""
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
        was, now = before.get("lifetime"), after.get("lifetime")
        if was is not None and now is not None:
            was_rank, now_rank = _rank(was), _rank(now)
            if now_rank is None:
                findings.append(
                    Violation(SURFACE, path, f"configuration key lifetime {now!r} is not a known lifetime", deprecated)
                )
            elif was_rank is not None and now_rank > was_rank:
                reason = f"configuration key lifetime moved later from {was} to {now}"
                findings.append(Violation(SURFACE, path, reason, deprecated))
    for path, after in head_keys.items():
        if path not in base_keys and after.get("required"):
            findings.append(Violation(SURFACE, path, "configuration key became required"))
    head_paths = {p["path"] for p in head.get("paths", [])}
    for entry in base.get("paths", []):
        if entry["path"] not in head_paths:
            findings.append(Violation(SURFACE, entry["path"], "durable path removed", bool(entry.get("deprecated"))))
    return findings
