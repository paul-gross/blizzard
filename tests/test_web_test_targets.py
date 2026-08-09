"""Every Angular `test` target excludes the shell-sweep specs (unit tier).

A project missing the exclude runs a real-Chromium spec inside the merge gate *and*, since
`structural-gate.js` scopes its real-timer sweep by the same filename, exempts it from that
sweep at once — the pair the sweep exists to keep apart (issue #275).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_ANGULAR = Path(__file__).resolve().parents[1] / "web" / "angular.json"
_SHELL_SWEEP_GLOB = "**/*.shell-sweep.spec.ts"


def test_every_test_target_excludes_the_shell_sweep_specs() -> None:
    projects = json.loads(_ANGULAR.read_text())["projects"]
    assert projects, "no Angular projects declared — the sweep below would be a green no-op"
    missing = [
        name
        for name, project in projects.items()
        if (target := (project.get("architect") or project.get("targets", {})).get("test")) is not None
        if _SHELL_SWEEP_GLOB not in (target.get("options", {}).get("exclude") or [])
    ]
    assert not missing, f"projects whose `test` target would run a shell-sweep spec: {missing}"
