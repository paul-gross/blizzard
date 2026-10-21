from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "verify-release.sh"
_TAG = "v0.2.0"
_VERSION = "0.2.0"
_REPO = "paul-gross/blizzard"

_GH = """\
#!/usr/bin/env bash
echo "gh $*" >> "$STUB_LOG"
case "$1 $2" in
  "--version "*) echo "gh version 2.102.0" ;;
  "attestation verify")
    [ -z "${STUB_NO_ATTESTATION:-}" ] || exit 1
    [ "$3" != "--help" ] || exit 0
    [ -z "${STUB_FAIL_ATTEST:-}" ] || [[ "$*" != *"$STUB_FAIL_ATTEST"* ]] || exit 1 ;;
  "api repos/"*)
    [ -z "${STUB_FAIL_COMPARE:-}" ] || exit 1
    echo "${STUB_COMPARE_STATUS:-ahead}" ;;
  "release view") cat "$STUB_DIR/page.txt" ;;
  "release download")
    pattern=""; dir=""
    while [ "$#" -gt 0 ]; do
      case "$1" in --pattern) pattern="$2"; shift ;; --dir) dir="$2"; shift ;; esac
      shift
    done
    for f in "$STUB_DIR"/files/*; do
      [ -z "$pattern" ] || [ "$(basename "$f")" = "$pattern" ] || continue
      cp "$f" "$dir/"
    done ;;
esac
"""

_COSIGN = """\
#!/usr/bin/env bash
echo "cosign $*" >> "$STUB_LOG"
[ -z "${STUB_FAIL_COSIGN:-}" ] || exit 1
"""

_UV = """\
#!/usr/bin/env bash
echo "uv $*" >> "$STUB_LOG"
case "$1" in
  run) while [ "$1" != "python" ]; do shift; done; shift; exec "$STUB_PYTHON" "$@" ;;
  venv)
    for last; do :; done
    mkdir -p "$last/bin"
    printf '#!/usr/bin/env bash\\necho "blizzard, version %s"\\n' "$STUB_REPORTED_VERSION" > "$last/bin/blizzard"
    chmod +x "$last/bin/blizzard" ;;
  pip) [ -z "${STUB_FAIL_SYNC:-}" ] || exit 1 ;;
esac
"""

_CURL = """\
#!/usr/bin/env bash
echo "curl $*" >> "$STUB_LOG"
case "$*" in
  *ghcr.io/token*) echo '{"token": "t"}' ;;
  *" -I "*) printf 'HTTP/2 200\\r\\nDocker-Content-Digest: %s\\r\\n' "$STUB_TAG_DIGEST" ;;
  *manifests/sha256:*) cat "$STUB_DIR/index.json" ;;
  *) exit 1 ;;
esac
"""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class _Release:
    def __init__(self, root: Path, version: str = _VERSION) -> None:
        self.version = version
        self.pep440 = version.replace("-rc.", "rc")
        self.dir = root / "stub"
        self.files = self.dir / "files"
        self.files.mkdir(parents=True)
        self.bin = root / "bin"
        self.bin.mkdir()
        self.log = root / "calls.log"
        self.log.touch()

        index = json.dumps(
            {
                "manifests": [
                    {"digest": "sha256:" + "a1" * 32, "platform": {"os": "linux", "architecture": "amd64"}},
                    {"digest": "sha256:" + "a2" * 32, "platform": {"os": "linux", "architecture": "arm64"}},
                    {"digest": "sha256:" + "ee" * 32, "platform": {"os": "unknown", "architecture": "unknown"}},
                ]
            }
        ).encode()
        (self.dir / "index.json").write_bytes(index)
        self.digest = "sha256:" + _sha(index)

        wheel = f"blizzard-{self.pep440}-py3-none-any.whl"
        (self.files / wheel).write_bytes(b"wheel")
        project = f"blizzard @ https://github.com/{_REPO}/releases/download/v{version}/{wheel} --hash=sha256:{_sha(b'wheel')}\n"
        (self.files / f"blizzard-{version}-requirements.txt").write_text("dep==1 --hash=sha256:00\n" + project)
        (self.files / f"blizzard-{version}-requirements-pg.txt").write_text("dep==1 --hash=sha256:00\n" + project)
        (self.files / f"blizzard-{version}.spdx.json").write_text("{}")
        (self.files / f"blizzard-hub-{version}-linux-amd64.spdx.json").write_text("{}")
        (self.files / f"blizzard-hub-{version}-linux-arm64.spdx.json").write_text("{}")
        (self.files / f"blizzard-hub-{version}-image.txt").write_text(
            f"ghcr.io/paul-gross/blizzard-hub@{self.digest}\n"
        )
        (self.files / "compat-breaks.json").write_text("{}")
        self.sum()

        for name, body in {"gh": _GH, "cosign": _COSIGN, "uv": _UV, "curl": _CURL}.items():
            stub = self.bin / name
            stub.write_text(body)
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "STUB_LOG": str(self.log),
            "STUB_DIR": str(self.dir),
            "STUB_PYTHON": sys.executable,
            "STUB_TAG_DIGEST": self.digest,
            "STUB_REPORTED_VERSION": self.pep440,
        }

    def sum(self) -> None:
        (self.files / "SHA256SUMS").unlink(missing_ok=True)
        lines = [f"{_sha(p.read_bytes())}  {p.name}" for p in sorted(self.files.iterdir())]
        (self.files / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        self.list_page()

    def list_page(self) -> None:
        (self.dir / "page.txt").write_text("".join(f"{p.name}\n" for p in sorted(self.files.iterdir())))

    def run(
        self, *args: str, script: Path = _SCRIPT, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(script), *args], capture_output=True, text=True, env={**self.env, **(env or {})}, cwd=self.dir
        )

    def calls(self) -> list[str]:
        return self.log.read_text().splitlines()


@pytest.fixture
def release(tmp_path: Path) -> _Release:
    return _Release(tmp_path)


@pytest.mark.parametrize("tag", [_TAG, "v0.2.0-rc.2", "v0.2.0-rc.3", "v1.0.0"])
def test_a_verifiable_release_passes_all_four_checks(release: _Release, tag: str) -> None:
    release = _Release(release.dir.parent / tag, tag[1:]) if tag != _TAG else release
    result = release.run(tag)
    assert result.returncode == 0, result.stderr
    assert [line for line in result.stdout.splitlines() if line.startswith("==")] == [
        "== 1. image",
        "== 2. image attestations",
        "== 3. attached files",
        "== 4. install",
    ]


def test_every_signature_and_attestation_uses_the_release_identity(release: _Release) -> None:
    assert release.run(_TAG).returncode == 0
    calls = [
        c for c in release.calls() if c.startswith(("cosign verify", "gh attestation verify")) and "--help" not in c
    ]
    assert len(calls) == 1 + 1 + 2 + 9 + 1, "image signature, image provenance, two SBOMs, each file, the wheel SBOM"
    for call in calls:
        assert "https://token.actions.githubusercontent.com" in call
        assert r"^https://github\.com/paul-gross/blizzard/\.github/workflows/release\.yml@refs/tags/v" in call


def test_the_sboms_are_verified_against_the_platform_digests_not_the_list(release: _Release) -> None:
    assert release.run(_TAG).returncode == 0
    sboms = [c for c in release.calls() if "--predicate-type https://spdx.dev/Document/v2.3" in c and "oci://" in c]
    assert [c.split("@")[1].split()[0] for c in sboms] == ["sha256:" + "a1" * 32, "sha256:" + "a2" * 32]


@pytest.mark.parametrize("args", [[], ["v0.2.0", "extra"]])
def test_the_wrong_argument_count_is_a_usage_error(release: _Release, args: list[str]) -> None:
    result = release.run(*args)
    assert result.returncode == 2
    assert "usage:" in result.stderr


@pytest.mark.parametrize("tag", ["0.2.0", "v0.2", "v0.2.0-beta.1", "v0.2.0-rc", "v0.2.0-rc.1.2", "v0.2.0rc2", "latest"])
def test_a_malformed_tag_is_a_usage_error(release: _Release, tag: str) -> None:
    result = release.run(tag)
    assert result.returncode == 2
    assert release.calls() == []


@pytest.mark.parametrize("tag", ["v0.1.0-rc.1", "v0.1.9", "v0.2.0-rc.1"])
def test_a_tag_below_the_floor_is_refused_by_name_before_any_tool_runs(release: _Release, tag: str) -> None:
    result = release.run(tag)
    assert result.returncode == 1
    assert "v0.2.0-rc.2" in result.stderr
    assert release.calls() == []


def test_a_release_cut_before_the_report_passes_without_it_whatever_its_version(release: _Release) -> None:
    (release.files / "compat-breaks.json").unlink()
    release.sum()
    result = release.run(_TAG, env={"STUB_COMPARE_STATUS": "behind"})
    assert result.returncode == 0, result.stderr
    assert f"compare/c46927dcb5850580e02668da6d16d09aabd74f9d...{_TAG}" in "\n".join(release.calls())


def test_a_missing_file_fails_naming_the_attached_files_check(release: _Release) -> None:
    (release.files / f"blizzard-{_VERSION}.spdx.json").unlink()
    release.sum()
    result = release.run(_TAG)
    assert result.returncode == 1
    assert "[attached files]" in result.stderr
    assert f"blizzard-{_VERSION}.spdx.json" in result.stderr


def test_an_extra_file_fails_naming_the_attached_files_check(release: _Release) -> None:
    (release.files / "notes.txt").write_text("x")
    release.sum()
    result = release.run(_TAG)
    assert result.returncode == 1
    assert "[attached files]" in result.stderr
    assert "notes.txt" in result.stderr


@pytest.mark.parametrize("status", ["behind", "diverged"])
def test_compat_breaks_is_an_extra_file_off_the_line_of_the_commit_that_added_it(
    release: _Release, status: str
) -> None:
    result = release.run(_TAG, env={"STUB_COMPARE_STATUS": status})
    assert result.returncode == 1
    assert "compat-breaks.json" in result.stderr


@pytest.mark.parametrize("status", ["ahead", "identical"])
def test_compat_breaks_is_required_from_the_commit_that_added_it(release: _Release, status: str) -> None:
    (release.files / "compat-breaks.json").unlink()
    release.sum()
    missing = release.run(_TAG, env={"STUB_COMPARE_STATUS": status})
    assert missing.returncode == 1
    assert "[attached files]" in missing.stderr
    assert "compat-breaks.json" in missing.stderr


def test_a_failed_comparison_fails_naming_the_attached_files_check(release: _Release) -> None:
    result = release.run(_TAG, env={"STUB_FAIL_COMPARE": "1"})
    assert result.returncode == 1
    assert "[attached files]" in result.stderr


def test_a_checksum_mismatch_fails_naming_the_attached_files_check(release: _Release) -> None:
    (release.files / f"blizzard-{_VERSION}.spdx.json").write_text('{"tampered": true}')
    release.list_page()
    result = release.run(_TAG)
    assert result.returncode == 1
    assert "[attached files]" in result.stderr
    assert "SHA256SUMS" in result.stderr


def test_a_requirements_file_naming_another_wheel_hash_fails(release: _Release) -> None:
    path = release.files / f"blizzard-{_VERSION}-requirements-pg.txt"
    path.write_text(path.read_text().replace(_sha(b"wheel"), "0" * 64))
    release.sum()
    result = release.run(_TAG)
    assert result.returncode == 1
    assert "[attached files]" in result.stderr
    assert f"blizzard-{_VERSION}-requirements-pg.txt" in result.stderr


@pytest.mark.parametrize(
    ("env", "check"),
    [
        ({"STUB_FAIL_COSIGN": "1"}, "[image]"),
        ({"STUB_TAG_DIGEST": "sha256:" + "00" * 32}, "[image]"),
        ({"STUB_FAIL_ATTEST": "oci://"}, "[image attestations]"),
        ({"STUB_FAIL_ATTEST": "SHA256SUMS"}, "[attached files]"),
        ({"STUB_FAIL_SYNC": "1"}, "[install]"),
        ({"STUB_REPORTED_VERSION": "9.9.9"}, "[install]"),
    ],
)
def test_a_failing_tool_exits_non_zero_naming_its_check(release: _Release, env: dict[str, str], check: str) -> None:
    result = release.run(_TAG, env=env)
    assert result.returncode == 1
    assert f"FAIL {check}" in result.stderr


def test_a_failed_check_stops_the_run_before_the_next_one(release: _Release) -> None:
    result = release.run(_TAG, env={"STUB_FAIL_COSIGN": "1"})
    assert result.returncode == 1
    assert not any(c.startswith("gh attestation verify") for c in release.calls() if "--help" not in c)
    assert "== 2." not in result.stdout


@pytest.mark.parametrize("missing", ["cosign", "uv", "gh"])
def test_a_missing_tool_is_named_before_any_check_runs(release: _Release, missing: str) -> None:
    (release.bin / missing).unlink()
    tools = release.dir.parent / "coreutils"
    tools.mkdir()
    for name in (
        "bash",
        "env",
        "mktemp",
        "rm",
        "head",
        "tail",
        "awk",
        "tr",
        "cut",
        "sort",
        "comm",
        "dirname",
        "basename",
    ):
        found = subprocess.run(["bash", "-c", f"command -v {name}"], capture_output=True, text=True).stdout.strip()
        if found:
            (tools / name).symlink_to(found)
    result = release.run(_TAG, env={"PATH": f"{release.bin}{os.pathsep}{tools}"})
    assert result.returncode == 1
    assert f"'{missing}' is not on PATH" in result.stderr
    assert "== 1." not in result.stdout


def test_a_gh_without_attestation_is_named_before_any_check_runs(release: _Release) -> None:
    result = release.run(_TAG, env={"STUB_NO_ATTESTATION": "1"})
    assert result.returncode == 1
    assert "no 'attestation' command" in result.stderr
    assert "== 1." not in result.stdout
