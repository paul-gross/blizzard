"""A ``blizzard-compat`` stub for tests of the tools that run it: ``releases --json`` and ``--tag`` answers."""

from __future__ import annotations

import json
import os
import stat
from collections.abc import Sequence
from pathlib import Path

import pytest


def stub_compat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    supported: Sequence[str] = (),
    breaks: Sequence[dict[str, str | None]] | None = None,
    tag_exit: int = 0,
) -> Path:
    """Put a ``blizzard-compat`` first on ``PATH``; returns its call log.

    ``releases --json`` lists ``supported``. ``--tag V`` writes ``compat-breaks.json`` for ``vV`` holding ``breaks`` into
    its working directory (when ``breaks`` is given), prints a line, and exits ``tag_exit``."""
    bin_dir = tmp_path.parent / f"{tmp_path.name}-compat-bin"
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "calls"
    releases = json.dumps({"releases": [{"tag": t} for t in supported]})
    script = bin_dir / "blizzard-compat"
    lines = [
        "#!/bin/sh",
        f"echo \"$@\" >> '{log}'",
        'if [ "$1" = releases ]; then',
        f"  echo '{releases}'",
        "  exit 0",
        "fi",
    ]
    if breaks is not None:
        body = json.dumps(breaks)
        lines.append(f'printf \'{{"tag": "v%s", "breaks": %s}}\\n\' "$2" \'{body}\' > compat-breaks.json')
    lines += ["echo 'compat ran'", f"exit {tag_exit}"]
    script.write_text("\n".join(lines) + "\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def entry(release: str, surface: str, where: str) -> dict[str, str | None]:
    """One ``compat-breaks.json`` element, as far as the cross-check reads it."""
    return {"release": release, "surface": surface, "where": where, "reason": "removed", "allowance": None}
