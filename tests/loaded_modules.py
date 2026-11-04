"""Helpers for the layering tests, shared with other test modules."""

from __future__ import annotations

import json
import os
import subprocess
import sys


def loaded_after_running(argv: list[str], env: dict[str, str] | None = None) -> set[str]:
    """The modules a ``blizzard`` invocation leaves loaded, in a fresh interpreter."""
    code = (
        "import sys, json; from click.testing import CliRunner; from blizzard.cli.main import blizzard; "
        f"CliRunner().invoke(blizzard, {argv!r}); print(json.dumps(sorted(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, env={**os.environ, **(env or {})}
    )
    return set(json.loads(result.stdout))
