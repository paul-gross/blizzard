from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.component

_FIXTURES = Path(__file__).parent / "fixtures" / "route_clause"
_ROOT = repo_root()
_VALE = shutil.which("vale")
_REQUIRED = os.environ.get("BLIZZARD_REQUIRE_VALE") == "1"

if _VALE is None and not _REQUIRED:
    pytest.skip("vale is not installed; `mise run process-ref-lint` runs this module with it", allow_module_level=True)

_HUB = "src/blizzard/hub/cli/sample.py"
_RUNNER = "src/blizzard/runner/cli/sample.py"
_ELSEWHERE = "src/blizzard/cli/sample.py"


def _hits(tmp_path: Path, fixture: str, at: str) -> list[dict]:
    assert _VALE is not None, "vale is required here: install it (mise install) or unset BLIZZARD_REQUIRE_VALE"
    shutil.copy(_ROOT / ".vale.ini", tmp_path / ".vale.ini")
    shutil.copytree(_ROOT / "styles", tmp_path / "styles")
    target = tmp_path / at
    target.parent.mkdir(parents=True)
    shutil.copy(_FIXTURES / fixture, target)
    out = subprocess.run([_VALE, "--output=JSON", at], cwd=tmp_path, capture_output=True, text=True, check=False)
    return json.loads(out.stdout).get(at, []) if out.stdout.strip() else []


def _route(alerts: list[dict]) -> list[dict]:
    return [a for a in alerts if a["Check"] == "Blizzard.RouteClause"]


@pytest.mark.parametrize("at", [_HUB, _RUNNER])
def test_every_audited_spelling_is_rejected_in_each_cli_directory(tmp_path: Path, at: str) -> None:
    assert len(_route(_hits(tmp_path, "positive.py.txt", at))) == 7


@pytest.mark.parametrize("at", [_HUB, _RUNNER])
def test_near_misses_and_status_handling_pass_in_each_cli_directory(tmp_path: Path, at: str) -> None:
    assert _route(_hits(tmp_path, "negative.py.txt", at)) == []


def test_the_clause_is_allowed_outside_the_two_cli_directories(tmp_path: Path) -> None:
    assert _route(_hits(tmp_path, "positive.py.txt", _ELSEWHERE)) == []


@pytest.mark.parametrize("at", [_HUB, _RUNNER])
def test_the_other_blizzard_rules_still_fire_in_the_cli_directories(tmp_path: Path, at: str) -> None:
    checks = {a["Check"] for a in _hits(tmp_path, "scope.py.txt", at)}
    assert {"Blizzard.RouteClause", "Blizzard.ChangeHistory"} <= checks
