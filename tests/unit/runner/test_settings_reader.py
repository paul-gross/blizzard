"""Runner setting selection, independent of projection and provenance storage."""

from pathlib import Path

import pytest

from blizzard.foundation.config_keys import Lifetime
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.host_config import HostConfig, RunnerEntry
from blizzard.runner.settings.ports import SettingsSnapshot
from blizzard.runner.settings_reader import RunnerEntriesJudge


@pytest.fixture
def judge(tmp_path: Path) -> RunnerEntriesJudge:
    return RunnerEntriesJudge(HostConfig(root=tmp_path, db_url="sqlite://"), RunnerEntry(name="held", id="runner-1"))


@pytest.mark.parametrize(
    ("entries", "selected", "source", "value_from"),
    [
        ({}, "claude_code.binary", "default", "resolved"),
        ({"harness_binary": Entry("legacy")}, "harness_binary", "entry", "resolved"),
        ({"harness_binary": Entry(None, at_default=True)}, "harness_binary", "default", "resolved"),
        (
            {"harness_binary": Entry("legacy"), "claude_code.binary": Entry("new")},
            "claude_code.binary",
            "entry",
            "entry",
        ),
        (
            {"harness_binary": Entry("legacy"), "claude_code.binary": Entry(None, at_default=True)},
            "claude_code.binary",
            "default",
            "resolved",
        ),
    ],
)
def test_alias_selection(
    judge: RunnerEntriesJudge, entries: dict[str, Entry], selected: str, source: str, value_from: str
) -> None:
    setting = judge.effective(SettingsSnapshot(4, entries))["claude_code.binary"]
    assert (setting.provenance_key, setting.entry, setting.source, setting.value_from) == (
        selected,
        entries.get(selected),
        source,
        value_from,
    )


def test_permission_mode_is_not_an_autonomy_substitute(judge: RunnerEntriesJudge) -> None:
    assert judge.aliases["harness_permission_mode"] == "harness.autonomy"
    assert not judge.alias_metadata["harness_permission_mode"].substitutes
    setting = judge.effective(SettingsSnapshot(1, {"harness_permission_mode": Entry("bypassPermissions")}))[
        "harness.autonomy"
    ]
    assert (setting.entry, setting.provenance_key, setting.source, setting.value_from) == (
        None,
        "harness.autonomy",
        "default",
        "resolved",
    )


def test_identity_and_membership(judge: RunnerEntriesJudge) -> None:
    settings = judge.effective(SettingsSnapshot(1, {"name": Entry(None, at_default=True), "unknown": Entry(1)}))
    assert "unknown" not in settings
    assert settings["id"].lifetime == Lifetime.IMMUTABLE
    assert (settings["id"].value_from, settings["id"].identity_value) == ("identity", "runner-1")
    assert (settings["name"].value_from, settings["name"].identity_value) == ("identity", "held")
    name = judge.effective(SettingsSnapshot(2, {"name": Entry("display")}))["name"]
    assert (name.value_from, name.entry, name.source) == ("entry", Entry("display"), "entry")
