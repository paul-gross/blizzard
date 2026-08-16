"""Every daemon config key is named by a merge-gating test (unit tier).

The floor, not the proof: naming a key is weaker than pinning its threading
(``tests/test_runner_loop_build.py``), but a *new* key with neither fails here (#276).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[1]

#: Test roots the merge gate does not run — `mise run gate` is unit + component only.
_NON_GATING = {"e2e", "journey", "crash", "service"}

#: Every operator-written config dataclass, not just the two roots: a key on a nested
#: block (a `[[work_source]]`, an `[[auth.oauth.provider]]`) is as droppable as a root one.
_CONFIGS = (
    ("runner", "RunnerConfig"),
    ("hub", "HubConfig"),
    ("hub", "WorkSourceConfig"),
    ("hub", "OAuthProviderConfig"),
    ("hub", "AuthConfig"),
    ("hub", "TranscriptCapsConfig"),
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
