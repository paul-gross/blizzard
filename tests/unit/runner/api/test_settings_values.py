"""Projection of parsed harness settings into the settings record vocabulary."""

from pathlib import Path

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.claude_code.section import ClaudeCodeSection
from blizzard.runner.harness.opencode.section import OpenCodeSection
from tests.support.harness_sections import sections


def test_harness_values_use_each_parsed_section() -> None:
    config = RunnerConfig(
        root=Path("."),
        db_url="sqlite://",
        harness_sections=sections(
            ClaudeCodeSection(binary="claude-custom", supported_models=("opus",), model_aliases=(("a", "opus"),)),
            OpenCodeSection(
                enabled=False,
                binary="oc-custom",
                auth_path="auth.json",
                worker_config_path="worker.json",
                supported_models=("vendor/model",),
                effort_aliases=(("high", "max"),),
            ),
        ),
    )
    values = {key: value for section in config.harness_sections for key, value in section.settings_values().items()}
    assert values["claude_code.binary"] == "claude-custom"
    assert values["models.supported"] == ("opus",)
    assert values["models.aliases"] == {"a": "opus"}
    assert values["opencode.enabled"] is False
    assert values["opencode.binary"] == "oc-custom"
    assert values["opencode.auth_path"] == "auth.json"
    assert values["opencode.worker_config_path"] == "worker.json"
    assert values["opencode.models.supported"] == ("vendor/model",)
    assert values["opencode.effort.aliases"] == {"high": "max"}
