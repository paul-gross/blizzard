"""``scripts/release-inventory.sh`` — assembles the files a release attaches and sums
them. Unit-tested beside ``tests/test_export_requirements.py`` because the file names,
the image line, and the checksum coverage are what a verifier of a release depends on.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "release-inventory.sh"
_DIGEST = "sha256:" + "ab" * 32


def _wheel(tmp_path: Path, pep440: str) -> Path:
    path = tmp_path / "wheel" / f"blizzard-{pep440}-py3-none-any.whl"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"not really a wheel")
    return path


def _run(*args: str | Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(_SCRIPT), *map(str, args)], capture_output=True, text=True)


@pytest.mark.parametrize(("version", "pep440"), [("0.7.0-rc.1", "0.7.0rc1"), ("0.7.0", "0.7.0")])
def test_the_attached_set_has_the_contract_file_names(tmp_path: Path, version: str, pep440: str) -> None:
    out = tmp_path / "inventory"
    result = _run(version, _wheel(tmp_path, pep440), _DIGEST, out)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in out.iterdir()) == sorted(
        [
            f"blizzard-{pep440}-py3-none-any.whl",
            f"blizzard-{version}-requirements.txt",
            f"blizzard-{version}-requirements-pg.txt",
            f"blizzard-hub-{version}-image.txt",
            "SHA256SUMS",
        ]
    )


def test_the_wheel_is_copied_byte_for_byte(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "0.7.0")
    out = tmp_path / "inventory"
    assert _run("0.7.0", wheel, _DIGEST, out).returncode == 0
    assert (out / wheel.name).read_bytes() == wheel.read_bytes()


def test_the_image_file_is_the_single_digest_line(tmp_path: Path) -> None:
    out = tmp_path / "inventory"
    assert _run("0.7.0", _wheel(tmp_path, "0.7.0"), _DIGEST, out).returncode == 0
    assert (out / "blizzard-hub-0.7.0-image.txt").read_text() == f"ghcr.io/paul-gross/blizzard-hub@{_DIGEST}\n"


def test_both_requirements_files_end_with_the_wheel_project_line(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "0.7.0")
    out = tmp_path / "inventory"
    assert _run("0.7.0", wheel, _DIGEST, out).returncode == 0
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    for name in ("blizzard-0.7.0-requirements.txt", "blizzard-0.7.0-requirements-pg.txt"):
        assert (out / name).read_text().splitlines()[-1].endswith(f"--hash=sha256:{digest}")
    assert re.search(r"^psycopg", (out / "blizzard-0.7.0-requirements-pg.txt").read_text(), re.MULTILINE)


def test_checksums_cover_every_other_file_sorted_and_verify(tmp_path: Path) -> None:
    out = tmp_path / "inventory"
    out.mkdir()
    (out / "blizzard-0.7.0.spdx.json").write_text("{}")
    assert _run("0.7.0", _wheel(tmp_path, "0.7.0"), _DIGEST, out).returncode == 0
    names = [line.split("  ", 1)[1] for line in (out / "SHA256SUMS").read_text().splitlines()]
    assert names == sorted(p.name for p in out.iterdir() if p.name != "SHA256SUMS")
    assert "blizzard-0.7.0.spdx.json" in names
    check = subprocess.run(["sha256sum", "-c", "SHA256SUMS"], cwd=out, capture_output=True, text=True)
    assert check.returncode == 0, check.stderr


def test_compat_breaks_is_attached_and_summed_only_when_the_path_exists(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path, "0.7.0")
    report = tmp_path / "compat-breaks.json"
    report.write_text("[]")
    with_report = tmp_path / "with"
    assert _run("0.7.0", wheel, _DIGEST, with_report, "--compat-breaks", report).returncode == 0
    assert (with_report / "compat-breaks.json").read_text() == "[]"
    assert "compat-breaks.json" in (with_report / "SHA256SUMS").read_text()

    without = tmp_path / "without"
    assert _run("0.7.0", wheel, _DIGEST, without, "--compat-breaks", tmp_path / "missing.json").returncode == 0
    assert not (without / "compat-breaks.json").exists()
    assert "compat-breaks.json" not in (without / "SHA256SUMS").read_text()


@pytest.mark.parametrize("digest", ["", "sha256:abc", "sha256:" + "AB" * 32, "md5:" + "ab" * 32, "ab" * 32])
def test_a_malformed_digest_is_refused(tmp_path: Path, digest: str) -> None:
    out = tmp_path / "inventory"
    result = _run("0.7.0", _wheel(tmp_path, "0.7.0"), digest, out)
    assert result.returncode != 0
    assert not out.exists()
