"""The committed OpenAPI descriptions carry only consumer-resolvable prose (unit tier).

The mechanical companion to ``bzh:comment-locality``'s wire-docstring clause: a description
naming a UI surface, an internal Python symbol, or workspace path notation fails here
rather than shipping as public API reference text.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SPECS = sorted((repo_root() / "openapi").glob("*.openapi.json"))

_SCHEMAS = {name for spec in _SPECS for name in json.loads(spec.read_text())["components"]["schemas"]}

# Public names a description may carry though they look internal.
_ADMITTED = {"BLIZZARD_LEASE_ID"}

_INTERNAL_PATH = re.compile(
    r":(?:class|mod|func|meth|attr|data):|(?:src|tests)/|\.py\b|::test_|``[A-Z]\w+\.\w+``|``\w+\.\w+\(\)``|\bbzh:\S+"
)
# A backticked name of two or more capitalized humps (a leading acronym counts), a backticked
# dotted path, or an UPPER_SNAKE token. Single-hump words, hyphenated headers and lowercase
# names fall outside by shape.
_HUMPS = re.compile(r"`{1,2}((?=\w*[a-z])(?:[A-Z][a-z0-9]*){2,})`{1,2}")
_DOTTED = re.compile(r"`{1,2}(\w+(?:\.\w+)+)`{1,2}")
_UPPER_SNAKE = re.compile(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")


def _internal_identifiers(text: str) -> list[str]:
    humps = [name for name in _HUMPS.findall(text) if name not in _SCHEMAS]
    dotted = [
        path
        for path in _DOTTED.findall(text)
        if path.split(".")[0] not in _SCHEMAS and any(segment[:1].isupper() for segment in path.split("."))
    ]
    snake = [name for name in _UPPER_SNAKE.findall(text) if name not in _ADMITTED]
    return [*humps, *dotted, *snake]


def _carries_internal_identifier(text: str) -> bool:
    return bool(_INTERNAL_PATH.search(text) or _internal_identifiers(text))


_FORBIDDEN = {
    "client-surface claim": lambda text: bool(
        re.search(r"\bboards?\b|\bthe UI\b|\bfrontend\b|\bdocks?\b|\bkiosk\b", text, re.IGNORECASE)
    ),
    "internal identifier": _carries_internal_identifier,
    "workspace path notation": lambda text: bool(
        re.search(r"\b(?:blizzard-context|blizzard-mock|blizzard-product|winter-\w+):", text)
    ),
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
        for kind, matches in _FORBIDDEN.items()
        if matches(text)
    ]
    assert not offenders, "\n".join(["descriptions carry unresolvable prose:", *offenders])


def test_every_spec_is_scanned() -> None:
    """A renamed or dropped spec file would otherwise reduce this guard to a green no-op."""
    assert {spec.name for spec in _SPECS} == {"hub.openapi.json", "runner.openapi.json"}


_WIRE = _SPECS[0].parent.parent / "src" / "blizzard" / "wire"


def _unschemaed_wire_models() -> list[tuple[str, str, str]]:
    """Every documented `(file, class, docstring)` in `wire/` that no committed spec publishes.

    Discovered by base class, not by *a* base class: an SSE payload derives from
    `SseFramePayload`, not `BaseModel` directly, and is as publishable as one. A private
    class is skipped — a `_`-prefixed Protocol is a structural alias no route can name, and
    its internal references are the point of it."""
    found = []
    for path in sorted(_WIRE.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef) or node.name.startswith("_"):
                continue
            doc = ast.get_docstring(node)
            if doc is None or node.name in _SCHEMAS or any(s.endswith(node.name) for s in _SCHEMAS):
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
        for kind, matches in _FORBIDDEN.items()
        if matches(doc)
    ]
    assert not offenders, "\n".join(["un-schema'd wire models carrying unresolvable prose:", *offenders])


def test_the_unschemaed_scan_is_not_restricted_to_direct_basemodel_subclasses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reach `bzh:comment-locality` claims, pinned rather than trusted. An SSE payload
    subclasses `SseFramePayload`, not `BaseModel` directly; a discovery narrowed to
    `class X(BaseModel)` — the obvious simplification — would drop every model shaped like
    it, silently."""
    (tmp_path / "frames.py").write_text(
        'class FramePayload(BaseModel):\n    """Base."""\n\n\nclass ProbeChangedPayload(FramePayload):\n    """Probe."""\n'
    )
    monkeypatch.setattr(sys.modules[__name__], "_WIRE", tmp_path)
    found = {(file, name) for file, name, _ in _unschemaed_wire_models()}
    assert ("frames.py", "ProbeChangedPayload") in found, sorted(found)
