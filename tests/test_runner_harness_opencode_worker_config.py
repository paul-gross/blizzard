"""The runner-owned OpenCode permission/plugin document — permission-only; the plugin's
own heartbeat and ``shell.env`` jobs are added separately."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.opencode.shapes import parse_worker_config
from blizzard.runner.harness.opencode.worker_config import render_worker_config, write_worker_config

pytestmark = pytest.mark.unit


def test_render_worker_config_denies_the_question_tool() -> None:
    # A headless worker has no one to answer OpenCode's native `question` tool;
    # `blizzard runner ask` is its lease-authenticated replacement.
    document = render_worker_config()
    parsed = parse_worker_config(document)
    assert parsed.permissions["question"] == "deny"
    assert parsed.plugins == ()


def test_render_worker_config_parses_as_the_pinned_worker_config_shape() -> None:
    document = render_worker_config()
    parsed = parse_worker_config(document)
    assert parsed.permissions["question"] == "deny"


def test_render_worker_config_carries_forward_declared_plugins() -> None:
    document = render_worker_config(plugins=("blizzard-heartbeat-plugin",))
    assert parse_worker_config(document).plugins == ("blizzard-heartbeat-plugin",)


def test_write_worker_config_persists_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "opencode-worker-config.json"

    write_worker_config(path)

    assert path.exists()
    parse_worker_config(json.loads(path.read_text()))
