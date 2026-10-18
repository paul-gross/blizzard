"""``scripts/export-requirements.sh`` — turns ``uv.lock`` into the hashed requirements
file an install hangs from. Unit-tested beside ``tests/test_image_tags.py`` because the
shape of the project line and the refusal on a stale lock are what a release depends on.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_ROOT = repo_root()
_SCRIPT = _ROOT / "scripts" / "export-requirements.sh"
_VERSION = "1.2.3"
_WHEEL_NAME = "blizzard-1.2.3-py3-none-any.whl"


@pytest.fixture(scope="module")
def wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("wheel") / _WHEEL_NAME
    path.write_bytes(b"not really a wheel")
    return path


def _run(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_SCRIPT), *map(str, args)], capture_output=True, text=True)


def test_the_last_line_pins_the_wheel_by_release_address_and_digest(wheel: Path) -> None:
    result = _run(_VERSION, wheel)
    assert result.returncode == 0, result.stderr
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    assert result.stdout.splitlines()[-1] == (
        f"blizzard @ https://github.com/paul-gross/blizzard/releases/download/v{_VERSION}/{_WHEEL_NAME}"
        f" --hash=sha256:{digest}"
    )


def test_every_dependency_is_pinned_and_hashed(wheel: Path) -> None:
    result = _run(_VERSION, wheel)
    assert result.returncode == 0, result.stderr
    assert re.search(r"^alembic==\S+ \\\n\s+--hash=sha256:[0-9a-f]{64}", result.stdout, re.MULTILINE)


def test_no_project_omits_the_project_line(wheel: Path) -> None:
    result = _run(_VERSION, wheel, "--no-project")
    assert result.returncode == 0, result.stderr
    assert "blizzard @" not in result.stdout


def test_extra_postgres_adds_the_driver(wheel: Path) -> None:
    base = _run(_VERSION, wheel, "--no-project")
    pg = _run(_VERSION, wheel, "--no-project", "--extra", "postgres")
    assert pg.returncode == 0, pg.stderr
    assert not re.search(r"^psycopg", base.stdout, re.MULTILINE)
    assert re.search(r"^psycopg", pg.stdout, re.MULTILINE)


def test_a_stale_lock_is_refused_with_no_output(wheel: Path, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(_SCRIPT, repo / "scripts" / _SCRIPT.name)
    # A symlink, not a copy: uv.lock is deliberately not a declared test read.
    (repo / "uv.lock").symlink_to(_ROOT / "uv.lock")
    pyproject = (_ROOT / "pyproject.toml").read_text()
    stale, count = re.subn(r"(?m)^version = \"[^\"]*\"", 'version = "99.0.0"', pyproject, count=1)
    assert count == 1
    (repo / "pyproject.toml").write_text(stale)
    result = subprocess.run(
        [str(repo / "scripts" / _SCRIPT.name), _VERSION, str(wheel)], capture_output=True, text=True
    )
    assert result.returncode != 0
    assert result.stdout == ""
    assert "uv.lock" in result.stderr


def test_a_missing_wheel_is_refused() -> None:
    result = _run(_VERSION, "/nonexistent/blizzard.whl")
    assert result.returncode != 0
