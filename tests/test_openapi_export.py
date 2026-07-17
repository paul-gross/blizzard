"""OpenAPI export smoke (unit tier).

The exporter is the single source of both specs the frontend's openapi-ts client
and CI's drift check consume. This asserts it writes deterministic,
non-empty specs for both daemons that include the health route.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.tools.openapi import export

pytestmark = pytest.mark.unit


def test_export_writes_both_specs(tmp_path: Path) -> None:
    written = export(tmp_path)

    names = {p.name for p in written}
    assert names == {"hub.openapi.json", "runner.openapi.json"}

    for path in written:
        spec = json.loads(path.read_text())
        assert spec["openapi"].startswith("3.")
        assert "/api/health" in spec["paths"]


def test_export_is_deterministic(tmp_path: Path) -> None:
    first = tmp_path / "a"
    second = tmp_path / "b"
    export(first)
    export(second)
    assert (first / "hub.openapi.json").read_text() == (second / "hub.openapi.json").read_text()
    assert (first / "runner.openapi.json").read_text() == (second / "runner.openapi.json").read_text()
