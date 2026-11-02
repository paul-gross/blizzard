"""The CLI surface: the command trees ``blizzard hub`` and ``blizzard runner`` commit as their golden.

Commands are keyed by full path; a group's ``aliases`` resolve to that group's ``get`` node, so a verb renamed to
``get`` with its old spelling kept as an alias is not a removal. Options are keyed by each spelling, arguments by
position, so renaming an argument's Python name is no break. A thing is deprecated when the baseline marks it, an
ancestor group, or (for an option) the option itself."""

from __future__ import annotations

from typing import Any

from blizzard.cli.get_group import GET_VERB
from blizzard.tools.compat.violation import Violation

SURFACE = "cli"


def _resolve(tree: dict[str, Any], name: str) -> dict[str, Any] | None:
    """The child of ``tree`` spelled ``name``, following a group's alias onto its ``get`` node."""
    children = tree.get("commands", {})
    if name in children:
        return children[name]
    if name in tree.get("aliases", []):
        return children.get(GET_VERB)
    return None


def _spellings(param: dict[str, Any]) -> set[str]:
    return {*param.get("opts", []), *param.get("secondary_opts", [])}


def _split(params: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return (
        [p for p in params if p["kind"] == "option"],
        [p for p in params if p["kind"] == "argument"],
    )


def _params(path: str, base: dict[str, Any], head: dict[str, Any], deprecated: bool) -> list[Violation]:
    findings: list[Violation] = []
    base_options, base_arguments = _split(base.get("params", []))
    head_options, head_arguments = _split(head.get("params", []))
    for option in base_options:
        marked = deprecated or bool(option.get("deprecated"))
        for spelling in sorted(_spellings(option)):
            match = next((h for h in head_options if spelling in _spellings(h)), None)
            where = f"{path} {spelling}"
            if match is None:
                findings.append(Violation(SURFACE, where, "option removed", marked))
                continue
            if match["type"] != option["type"]:
                reason = f"option type changed from {option['type']} to {match['type']}"
                findings.append(Violation(SURFACE, where, reason, marked))
            if match["required"] and not option["required"]:
                findings.append(Violation(SURFACE, where, "option became required", marked))
    base_spellings = {s for o in base_options for s in _spellings(o)}
    for option in head_options:
        if option["required"] and not _spellings(option) & base_spellings:
            findings.append(Violation(SURFACE, f"{path} {sorted(_spellings(option))[0]}", "required option added"))
    for index, argument in enumerate(base_arguments, start=1):
        where = f"{path} <argument {index}>"
        if index > len(head_arguments):
            findings.append(Violation(SURFACE, where, "argument removed", deprecated))
            continue
        match = head_arguments[index - 1]
        if match["type"] != argument["type"]:
            reason = f"argument type changed from {argument['type']} to {match['type']}"
            findings.append(Violation(SURFACE, where, reason, deprecated))
        if match["required"] and not argument["required"]:
            findings.append(Violation(SURFACE, where, "argument became required", deprecated))
    for index, argument in enumerate(head_arguments, start=1):
        if index > len(base_arguments) and argument["required"]:
            findings.append(Violation(SURFACE, f"{path} <argument {index}>", "required argument added"))
    return findings


def _walk(base: dict[str, Any], head: dict[str, Any], deprecated: bool) -> list[Violation]:
    path = base["path"]
    deprecated = deprecated or bool(base.get("deprecated"))
    findings = _params(path, base, head, deprecated)
    for name, child in base.get("commands", {}).items():
        target = _resolve(head, name)
        if target is None:
            marked = deprecated or bool(child.get("deprecated"))
            findings.append(Violation(SURFACE, child["path"], "command removed", marked))
            continue
        findings += _walk(child, target, deprecated)
    for alias in base.get("aliases", []):
        if _resolve(head, alias) is None:
            findings.append(Violation(SURFACE, f"{path} {alias}", "command alias removed", deprecated))
    return findings


def classify_cli(base: dict[str, Any], head: dict[str, Any]) -> list[Violation]:
    """A command or alias removed, an option or argument removed, retyped, or made required, a required one added."""
    return _walk(base, head, False)
