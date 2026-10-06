"""A hub whose file and environment still carry the retired forge settings boots and ignores them (component tier)."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.hub import app as hub_app
from blizzard.hub import runtime as hub_runtime
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME

pytestmark = pytest.mark.component

_LEGACY_BLOCK = """
[[work_source]]
name = "blizzard"
provider = "github"
repo = "paul-gross/blizzard"
token_env = "BZ_WORK_SOURCE_TOKEN"
"""


def test_legacy_work_source_blocks_and_forge_variables_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = hub_runtime.init_environment(tmp_path / "hub")
    config.config_path.write_text(config.config_path.read_text() + _LEGACY_BLOCK)
    monkeypatch.delenv("BZ_WORK_SOURCE_TOKEN", raising=False)
    monkeypatch.setenv("BZ_FORGE_URL", "http://forge.invalid")
    monkeypatch.setenv("BZ_FORGE_TOKEN", "leftover")
    monkeypatch.setenv("BZ_FORGE_OWNER", "leftover")
    monkeypatch.setenv("BZ_FORGE_BASE_BRANCH", "leftover")

    app = hub_app.build_hosted_app(hub_app.HubConfig.load(config.root))

    captured = capsys.readouterr()
    assert "ignoring [[work_source]] blocks" in captured.out + captured.err
    services = app.state.services
    assert services.work_sources.names() == [RESERVED_HUB_SOURCE_NAME]
