"""The egress contract surface: the datasets and columns in ``dictionary.json``.

A dataset is identified by name and major version; a new major written beside the old is additive."""

from __future__ import annotations

from typing import Any

from blizzard.tools.compat.violation import Violation

SURFACE = "egress"


def _datasets(document: dict[str, Any]) -> dict[tuple[str, int], dict[str, Any]]:
    return {(d["name"], d["major_version"]): d for d in document.get("datasets", [])}


def classify_egress(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A column removed or retyped, a nullable column made required, and a dataset major no longer written."""
    findings: list[Violation] = []
    head_datasets = _datasets(head)
    for (name, major), dataset in _datasets(base).items():
        label = f"{name} v{major}"
        after = head_datasets.get((name, major))
        if after is None:
            findings.append(
                Violation(SURFACE, label, "dataset major no longer written", dataset.get("deprecated") is True)
            )
            continue
        head_columns = {c["name"]: c for c in after.get("columns", [])}
        for column in dataset.get("columns", []):
            where = f"{label}.{column['name']}"
            deprecated = column.get("deprecated") is True or dataset.get("deprecated") is True
            now = head_columns.get(column["name"])
            if now is None:
                findings.append(Violation(SURFACE, where, "column removed", deprecated))
                continue
            if column.get("type") != now.get("type"):
                reason = f"column type changed from {column.get('type')} to {now.get('type')}"
                findings.append(Violation(SURFACE, where, reason, deprecated))
            if column.get("nullable") and not now.get("nullable"):
                findings.append(Violation(SURFACE, where, "nullable column became required", deprecated))
    return findings
