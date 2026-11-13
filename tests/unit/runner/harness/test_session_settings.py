from dataclasses import FrozenInstanceError

import pytest

from blizzard.foundation.harness_telemetry_outcome import HarnessTelemetryOutcome
from blizzard.runner.harness.harness_telemetry_plan import HarnessTelemetryPlan
from blizzard.runner.harness.session_settings import SessionEnvironment, SessionSettings


def test_session_stamps_are_frozen_values() -> None:
    stamps = SessionSettings("provider/model", "high", "200k", environment=SessionEnvironment((), "/bin/bash"))
    assert stamps == SessionSettings("provider/model", "high", "200k", environment=SessionEnvironment((), "/bin/bash"))
    with pytest.raises(FrozenInstanceError):
        stamps.__setattr__("model", "other")


def test_judge_combines_session_location_with_current_launch_values() -> None:
    old = SessionSettings(
        binary="old-bin",
        config_dir="old-home",
        settings_path="old-settings",
        settings_argv=("old-arg",),
        bundle_path="old-bundle",
        transcript_root="old-transcripts",
        driver_command=("old-driver",),
        environment=SessionEnvironment((("PATH", "old-path"), ("HOME", "old-home")), "/old-shell"),
        model_aliases=(("tier", "old-model"),),
        prompt_prefix="old-prompt",
    )
    current = SessionSettings(
        binary="new-bin",
        config_dir="new-home",
        settings_path="new-settings",
        settings_argv=("new-arg",),
        bundle_path="new-bundle",
        transcript_root="new-transcripts",
        driver_command=("new-driver",),
        environment=SessionEnvironment((("PATH", "new-path"), ("HOME", "new-home")), "/new-shell"),
        model_aliases=(("tier", "new-model"),),
        prompt_prefix="new-prompt",
        telemetry=HarnessTelemetryPlan(traces=HarnessTelemetryOutcome.CAPTURED),
    )
    judge = current.for_session(old)
    assert (judge.binary, judge.config_dir, judge.settings_path, judge.settings_argv) == (
        "old-bin",
        "old-home",
        "old-settings",
        ("old-arg",),
    )
    assert (judge.bundle_path, judge.transcript_root, judge.driver_command) == (
        "old-bundle",
        "old-transcripts",
        ("old-driver",),
    )
    assert judge.environment.variables == {"PATH": "new-path", "HOME": "old-home"}
    assert judge.environment.shell == "/new-shell"
    assert judge.model_aliases == current.model_aliases
    assert judge.prompt_prefix == "new-prompt"
    assert judge.telemetry == current.telemetry


def test_judge_restores_absent_session_home_variables_without_losing_current_profile() -> None:
    origin = SessionSettings(
        environment=SessionEnvironment((("XDG_DATA_HOME", "/old/data"),), "/old/shell"),
    )
    current = SessionSettings(
        environment=SessionEnvironment(
            (("HOME", "/new/home"), ("XDG_DATA_HOME", "/new/data"), ("PATH", "/new/bin")), "/new/shell"
        ),
    )

    judge = current.for_session(origin)

    assert judge.environment.variables == {"XDG_DATA_HOME": "/old/data", "PATH": "/new/bin"}
    assert judge.environment.shell == "/new/shell"
