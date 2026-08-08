"""The committed OpenAPI descriptions carry only consumer-resolvable prose (unit tier).

The mechanical companion to ``bzh:comment-locality``'s wire-docstring clause: a description
naming a UI surface, an internal Python symbol, or workspace path notation fails here
rather than shipping as public API reference text (issue #278).
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SPECS = sorted((Path(__file__).resolve().parents[1] / "openapi").glob("*.openapi.json"))

_FORBIDDEN = {
    "client-surface claim": re.compile(r"\bboards?\b|\bthe UI\b|\bfrontend\b|\bdocks?\b|\bkiosk\b", re.IGNORECASE),
    "internal identifier": re.compile(
        r":(?:class|mod|func|meth|attr):|(?:src|tests)/|\.py\b|::test_|``[A-Z]\w+\.\w+``|``\w+\.\w+\(\)``"
    ),
    "workspace path notation": re.compile(r"\b(?:blizzard-context|blizzard-mock|blizzard-product|winter-\w+):"),
}


def _descriptions(node: object, path: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "description" and isinstance(value, str):
                yield path, value
            else:
                yield from _descriptions(value, f"{path}/{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from _descriptions(value, f"{path}/{index}")


@pytest.mark.parametrize("spec", _SPECS, ids=lambda spec: spec.name)
def test_descriptions_state_only_consumer_resolvable_facts(spec: Path) -> None:
    offenders = [
        f"{spec.name}{pointer} [{kind}]: {text!r}"
        for pointer, text in _descriptions(json.loads(spec.read_text()))
        for kind, pattern in _FORBIDDEN.items()
        if pattern.search(text)
    ]
    assert not offenders, "\n".join(["descriptions carry unresolvable prose:", *offenders])


def test_every_spec_is_scanned() -> None:
    """A renamed or dropped spec file would otherwise reduce this guard to a green no-op."""
    assert {spec.name for spec in _SPECS} == {"hub.openapi.json", "runner.openapi.json"}


_WIRE = _SPECS[0].parent.parent / "src" / "blizzard" / "wire"


def _unschemaed_wire_models() -> list[tuple[str, str, str]]:
    """Every documented `(file, class, docstring)` in `wire/` that no committed spec publishes.

    Discovered by base class, not by *a* base class: an SSE payload derives from
    `SseFramePayload` and a history row from `HistoryRow`, and each is as publishable as a
    direct `BaseModel`. A private class is skipped — a `_`-prefixed Protocol is a structural
    alias no route can name, and its internal references are the point of it."""
    schemas = {name for spec in _SPECS for name in json.loads(spec.read_text())["components"]["schemas"]}
    found = []
    for path in sorted(_WIRE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            doc = ast.get_docstring(node)
            if doc is None or node.name in schemas or any(s.endswith(node.name) for s in schemas):
                continue
            found.append((path.name, node.name, doc))
    return found


def test_wire_models_no_spec_reaches_are_held_to_the_same_bar() -> None:
    """The scope boundary, closed rather than declared: a wire model no route names as a
    response model never reaches a spec, so the scan above cannot see its docstring — yet
    it is one `responses=` away from becoming public. Held to the same three shapes here."""
    offenders = [
        f"{file}::{name} [{kind}]"
        for file, name, doc in _unschemaed_wire_models()
        for kind, pattern in _FORBIDDEN.items()
        if pattern.search(doc)
    ]
    assert not offenders, "\n".join(["un-schema'd wire models carrying unresolvable prose:", *offenders])


def test_the_unschemaed_scan_is_not_restricted_to_direct_basemodel_subclasses() -> None:
    """The reach `bzh:comment-locality` claims, pinned rather than trusted. `HistoryRow` is
    documented and subclasses nothing; a discovery narrowed to `class X(BaseModel)` — the
    obvious simplification — would drop it and every model shaped like it, silently."""
    found = {(file, name) for file, name, _ in _unschemaed_wire_models()}
    assert ("history.py", "HistoryRow") in found, sorted(found)
