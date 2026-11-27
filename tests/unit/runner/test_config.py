"""Projection of runner settings from the parsed configuration."""

from pathlib import Path

from blizzard.foundation.config_keys import Lifetime
from blizzard.runner.config import RunnerConfig
from blizzard.runner.config_keys import HOST_REGISTRY
from blizzard.runner.throttle.settings import DEFAULT_BUDGET_WINDOW_HOURS, DEFAULT_PACE_LEAD_PCT, HoldOrRelease
from tests.support.config_inputs import load_host_config
from tests.support.host_files import write_minimal_host


def test_settings_values_cover_registry_and_parser_defaults(tmp_path: Path) -> None:
    values = RunnerConfig(tmp_path, "sqlite://").settings_values()
    expected = {
        key.path.removeprefix("runner[].").split("[].", 1)[0]
        for key in HOST_REGISTRY.keys
        if key.path.startswith("runner[].")
        and not key.deprecated
        and (key.lifetime in (Lifetime.PASS, Lifetime.SESSION) or key.path == "runner[].id")
    }
    assert set(values) == expected
    assert values["throttle.pace.lead_pct"] == DEFAULT_PACE_LEAD_PCT
    assert values["throttle.budget.window_hours"] == DEFAULT_BUDGET_WINDOW_HOURS
    assert values["throttle.on_decline"] == HoldOrRelease.HOLD.value
    assert values["throttle.on_usage_limit"] == HoldOrRelease.HOLD.value
    assert values["throttle.pace.taper"] == []


def test_settings_values_project_loaded_values(tmp_path: Path) -> None:
    write_minimal_host(tmp_path, runner='[runner.worker]\nshell = "/bin/sh"\n')
    values = load_host_config(tmp_path).runner_config({}).settings_values()
    assert values["worker.shell"] == "/bin/sh"
    assert values["agents"] == 0
