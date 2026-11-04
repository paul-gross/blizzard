"""Real git worktrees in an ordinary folder; no winter CLI or workspace config."""

from __future__ import annotations

import subprocess
from pathlib import Path


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)
