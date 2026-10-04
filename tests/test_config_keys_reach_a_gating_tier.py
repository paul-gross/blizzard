"""Every daemon config key is named by a merge-gating test (unit tier).

The floor, not the proof: naming a key is weaker than pinning its threading
(``tests/test_runner_loop_build.py``), but a *new* key with neither fails here (#276).
"""

from __future__ import annotations

import ast
import dataclasses
import re

import pytest

from blizzard.runner.harness.sections import HARNESS_SECTION_KINDS
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_ROOT = repo_root()

#: Test roots the merge gate does not run — `mise run gate` is unit + component only.
_NON_GATING = {"e2e", "journey", "crash", "service"}

#: Every operator-written config dataclass, not just the two roots: a key on a nested
#: block (a `[[work_source]]`, an `[[auth.oauth.provider]]`) is as droppable as a root one.
_CONFIGS = (
    ("runner", "RunnerConfig"),
    ("runner", "SubscriptionDeclaration"),
    ("hub", "HubConfig"),
    ("hub", "WorkSourceConfig"),
    ("hub", "OAuthProviderConfig"),
    ("hub", "AuthConfig"),
    ("hub", "TranscriptCapsConfig"),
    ("hub", "EgressConfig"),
    ("foundation/trace_export", "TracingConfig"),
)


def _fields(daemon: str, name: str) -> list[str]:
    source = (_ROOT / "src" / "blizzard" / daemon / "config.py").read_text()
    node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef) and n.name == name)
    return [
        target.id
        for entry in node.body
        if isinstance(entry, ast.AnnAssign) and isinstance(target := entry.target, ast.Name)
        if not target.id.startswith("_")
    ]


def _gating_test_text() -> str:
    tests = _ROOT / "tests"
    return "\n".join(
        path.read_text() for path in tests.rglob("*.py") if path.relative_to(tests).parts[0] not in _NON_GATING
    )


@pytest.mark.parametrize(("daemon", "name"), _CONFIGS, ids=[name for _, name in _CONFIGS])
def test_every_config_key_is_named_by_a_gating_test(daemon: str, name: str) -> None:
    gating = _gating_test_text()
    unnamed = [field for field in _fields(daemon, name) if not re.search(rf"\b{re.escape(field)}\b", gating)]
    assert not unnamed, f"{name} keys no gating-tier test names: {unnamed}"


#: Every declared harness binding's own config section, so a binding added to the catalog is scanned too.
_SECTIONS = {kind.harness_id: kind.default() for kind in HARNESS_SECTION_KINDS}


@pytest.mark.parametrize("harness_id", list(_SECTIONS))
def test_every_harness_section_key_is_named_by_a_gating_test(harness_id: str) -> None:
    gating = _gating_test_text()
    section = _SECTIONS[harness_id]
    assert dataclasses.is_dataclass(section), f"{harness_id}'s section is not a dataclass"
    name = type(section).__name__
    fields = [field.name for field in dataclasses.fields(section) if not field.name.startswith("_")]
    unnamed = [field for field in fields if not re.search(rf"\b{re.escape(field)}\b", gating)]
    assert fields, f"{name} declares no keys"
    assert not unnamed, f"{name} keys no gating-tier test names: {unnamed}"
