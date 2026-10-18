"""The trace contract surface: span names, event names, and attributes in ``dictionary.json``."""

from __future__ import annotations

from typing import Any

from blizzard.tools.compat.violation import Violation

SURFACE = "trace"
_ATTRIBUTE_GROUPS = (("attributes", "attribute"), ("resource_attributes", "resource attribute"))


def _named(entries: list[Any]) -> dict[str, dict[str, Any]]:
    """Entries by name; an event may be written as the bare name."""
    return {e if isinstance(e, str) else e["name"]: {} if isinstance(e, str) else e for e in entries}


def _attribute_groups(document: dict[str, Any]) -> list[tuple[str, list[Any]]]:
    groups = [(label, document.get(key, [])) for key, label in _ATTRIBUTE_GROUPS]
    groups.append(("platform attribute", document.get("platform", {}).get("attributes", [])))
    return groups


def classify_traces(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A span, event, or attribute removed, and an attribute whose type changed."""
    findings: list[Violation] = []
    head_spans, head_events = _named(head.get("spans", [])), _named(head.get("event_names", []))
    for name, entry in _named(base.get("spans", [])).items():
        if name not in head_spans:
            findings.append(Violation(SURFACE, name, "span removed", entry.get("deprecated") is True))
    for name, entry in _named(base.get("event_names", [])).items():
        if name not in head_events:
            findings.append(Violation(SURFACE, name, "event removed", entry.get("deprecated") is True))
    head_groups = dict(_attribute_groups(head))
    for label, entries in _attribute_groups(base):
        head_attributes = _named(head_groups[label])
        for name, entry in _named(entries).items():
            deprecated = entry.get("deprecated") is True
            after = head_attributes.get(name)
            if after is None:
                findings.append(Violation(SURFACE, name, f"{label} removed", deprecated))
            elif entry.get("type") != after.get("type"):
                reason = f"{label} type changed from {entry.get('type')} to {after.get('type')}"
                findings.append(Violation(SURFACE, name, reason, deprecated))
    return findings
