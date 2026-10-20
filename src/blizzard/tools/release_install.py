"""A published release's wheel installed into its own virtualenv, and the verbs it runs — always as subprocesses.

Each installs as the hub image does: the release's hashed ``-requirements-pg.txt``, then the wheel with
``--no-deps``. Nothing here imports the installation, so a release's code never runs in this process."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import collaborator


@collaborator
@dataclass(frozen=True)
class Installation:
    """One virtualenv holding one blizzard wheel."""

    label: str
    bin_dir: Path

    def run(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.bin_dir / args[0]), *args[1:]], capture_output=True, text=True, env=env, check=False, timeout=300
        )

    def env(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        """This process's environment with this installation's bin dir leading ``PATH``."""
        return {**os.environ, "PATH": f"{self.bin_dir}{os.pathsep}{os.environ.get('PATH', '')}", **(extra or {})}


def install(label: str, source: Path, venv: Path) -> Installation:
    """Install the one wheel and ``-requirements-pg.txt`` in ``source`` into a fresh ``venv``."""
    (wheel,) = source.glob("*.whl")
    (requirements,) = source.glob("*-requirements-pg.txt")
    uv = shutil.which("uv") or "uv"
    python = ["--python", str(venv / "bin" / "python")]
    for args in (
        [uv, "venv", "--quiet", "--python", sys.executable, str(venv)],
        [uv, "pip", "install", "--quiet", *python, "--require-hashes", "-r", str(requirements)],
        [uv, "pip", "install", "--quiet", *python, "--no-deps", str(wheel)],
    ):
        done = subprocess.run(args, capture_output=True, text=True, check=False)
        assert done.returncode == 0, f"installing {label} failed: {' '.join(args)}\n{done.stderr}"
    return Installation(label, venv / "bin")


def download_release(tag: str, into: Path) -> Path:
    """The release's wheel and Postgres requirements, as its GitHub Release published them."""
    into.mkdir(parents=True, exist_ok=True)
    if not list(into.glob("*.whl")):
        done = subprocess.run(
            ["gh", "release", "download", tag, "--dir", str(into), "--pattern", "*.whl",
             "--pattern", "*-requirements-pg.txt"],
            capture_output=True, text=True, check=False,
        )  # fmt: skip
        assert done.returncode == 0, f"gh release download {tag} failed:\n{done.stderr}"
    return into
