from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.component

_FIXTURES = Path(__file__).parent / "fixtures" / "ui_surface"
_ROOT = repo_root()
_VALE = shutil.which("vale")
_REQUIRED = os.environ.get("BLIZZARD_REQUIRE_VALE") == "1"

if _VALE is None and not _REQUIRED:
    pytest.skip("vale is not installed; `mise run process-ref-lint` runs this module with it", allow_module_level=True)

_GATED = [
    "src/blizzard/wire/sample.py",
    "src/blizzard/foundation/sample.py",
    "src/blizzard/foundation/nested/sample.py",
    "src/blizzard/auth_core/sample.py",
]
_UNGATED = [
    "src/blizzard/hub/sample.py",
    "src/blizzard/runner/sample.py",
    "src/blizzard/cli/sample.py",
    "tests/sample.py",
]


def _ui(tmp_path: Path, fixture: str, at: str) -> list[dict]:
    assert _VALE is not None, "vale is required here: install it (mise install) or unset BLIZZARD_REQUIRE_VALE"
    shutil.copy(_ROOT / ".vale.ini", tmp_path / ".vale.ini")
    shutil.copytree(_ROOT / "styles", tmp_path / "styles")
    target = tmp_path / at
    target.parent.mkdir(parents=True)
    shutil.copy(_FIXTURES / fixture, target)
    out = subprocess.run([_VALE, "--output=JSON", at], cwd=tmp_path, capture_output=True, text=True, check=False)
    alerts = json.loads(out.stdout).get(at, []) if out.stdout.strip() else []
    return [a for a in alerts if a["Check"] == "Blizzard.UiSurface"]


@pytest.mark.parametrize("at", _GATED)
def test_every_ui_surface_word_is_rejected_in_each_shared_seam(tmp_path: Path, at: str) -> None:
    assert len(_ui(tmp_path, "positive.py.txt", at)) == 6


@pytest.mark.parametrize("at", _GATED)
def test_neutral_prose_and_code_literals_pass_in_each_shared_seam(tmp_path: Path, at: str) -> None:
    assert _ui(tmp_path, "negative.py.txt", at) == []


@pytest.mark.parametrize("at", _UNGATED)
def test_ui_surface_words_are_allowed_outside_the_shared_seams(tmp_path: Path, at: str) -> None:
    assert _ui(tmp_path, "positive.py.txt", at) == []
