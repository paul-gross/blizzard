"""The event-stream surface: the frame kinds and payload fields each daemon's stream carries.

A scope's document is composed from its manifest and the golden cases of each kind it lists. A kind's field set
is the union of keys across its cases; cases are examples and declare no required-ness, so only a vanished kind or
field is judged. The manifest's optional ``deprecated`` list names ``<kind>`` and ``<kind>.<field>`` entries."""

from __future__ import annotations

from typing import Any

from blizzard.tools.compat.violation import Violation

SURFACE = "event-stream"


def _fields(cases: dict[str, Any]) -> set[str]:
    return {key for payload in cases.values() for key in payload}


def _scope(document: dict[str, Any]) -> str:
    return str(document.get("scope", "hub"))


def classify_sse(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A frame kind removed from the manifest, and a payload field no case of its kind carries any longer."""
    scope = _scope(head)
    marked = set(base.get("manifest", {}).get("deprecated", []))
    head_kinds = set(head.get("manifest", {}).get("kinds", []))
    findings: list[Violation] = []
    for kind in base.get("manifest", {}).get("kinds", []):
        if kind not in head_kinds:
            findings.append(Violation(SURFACE, f"{scope} {kind}", "frame kind removed", kind in marked))
            continue
        head_fields = _fields(head.get("cases", {}).get(kind, {}))
        for field in sorted(_fields(base.get("cases", {}).get(kind, {})) - head_fields):
            deprecated = kind in marked or f"{kind}.{field}" in marked
            findings.append(Violation(SURFACE, f"{scope} {kind}.{field}", "frame field removed", deprecated))
    return findings
