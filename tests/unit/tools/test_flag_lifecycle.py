"""Release-snapshot lifecycle rules."""

from __future__ import annotations

import pytest
import yaml

from blizzard.foundation.flags.registry import FlagRegistry
from blizzard.tools.compat.releases import parse_release
from blizzard.tools.flag_lifecycle_domain import Mode, Snapshot, derive
from tests.support.repo_files import repo_root


def registry(*, on: bool | None = None, retired: bool = False) -> FlagRegistry:
    text = (
        f'[flag.alpha]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "{"on" if on else "off"}"\n'
        if on is not None
        else ""
    )
    if retired:
        text += '[retired]\nalpha = "hub"\n'
    return FlagRegistry.parse(text, "fixture")


def snapshot(tag: str, *, on: bool | None = None, retired: bool = False) -> Snapshot:
    release = parse_release(tag)
    assert release is not None
    return Snapshot(release, registry(on=on, retired=retired))


@pytest.mark.parametrize("mode", ["pr", "cut", "tag"])
def test_initial_on_and_stable_reversal_fail(mode: Mode) -> None:
    _, findings = derive([snapshot("v1.0.0-rc.1", on=True), snapshot("v1.0.0", on=True)], registry(on=False), mode)
    assert [(f.rule, f.severity) for f in findings] == [("initial-on", "FAIL"), ("post-stable-reversal", "FAIL")]


@pytest.mark.parametrize("mode,expected", [("pr", "WARN"), ("cut", "FAIL"), ("tag", "FAIL")])
def test_due_and_on_live(mode: Mode, expected: str) -> None:
    releases = [snapshot("v1.0.0-rc.1", on=False), snapshot("v1.0.0", on=True), snapshot("v1.1.0", on=True)]
    rows, findings = derive(releases, registry(on=True), mode)
    assert rows[0].due
    assert [(f.rule, f.severity) for f in findings] == [("due-live", expected)]


def test_candidate_reversal_and_on_not_due() -> None:
    releases = [
        snapshot("v1.0.0-rc.1", on=False),
        snapshot("v1.0.0-rc.2", on=True),
        snapshot("v1.0.0-rc.3", on=False),
        snapshot("v1.0.0", on=True),
    ]
    rows, findings = derive(releases, registry(on=True), "pr")
    assert rows[0].off_for == 2
    assert not rows[0].due
    assert [(f.rule, f.severity) for f in findings] == [("on-live", "WARN")]
    assert derive(releases, registry(on=True), "cut")[1] == []
    assert derive(releases, registry(on=True), "tag")[1] == []


def test_twelve_off_releases_and_historical_retirement() -> None:
    releases = [snapshot(f"v1.{i}.0", on=False) for i in range(12)]
    assert [(f.rule, f.severity) for f in derive(releases, registry(on=False), "cut")[1]] == [
        ("off-twelve-releases", "WARN")
    ]
    retired_rows, retired_findings = derive(
        [*releases, snapshot("v1.12.0", retired=True)],
        registry(retired=True),
        "pr",
        supported=frozenset({"v1.11.0", "v1.12.0"}),
    )
    assert retired_rows[0].off_for == 12
    assert retired_rows[0].retired_in == "v1.12.0"
    assert retired_findings == []
    history = [
        snapshot("v1.0.0", on=False),
        snapshot("v1.1.0", retired=True),
        snapshot("v1.2.0"),
        snapshot("v1.3.0", on=False),
    ]
    rows, findings = derive(history, registry(on=False), "tag")
    assert rows[0].retired_in == "v1.1.0"
    assert [f.rule for f in findings] == ["retired-name-reuse", "retired-name-reuse"]


def test_head_new_on_and_no_registry_history() -> None:
    assert derive([snapshot("v1.0.0")], registry(on=True), "pr")[1][0].rule == "initial-on"
    for mode in ("pr", "cut", "tag"):
        assert derive([], registry(on=True), mode)[1] == []


def test_tombstone_removable_once_no_supported_release_holds_it() -> None:
    releases = [snapshot("v1.0.0", on=False), snapshot("v1.1.0", retired=True)]
    head = registry(retired=True)
    held = derive(releases, head, "pr", supported=frozenset({"v1.0.0", "v1.1.0"}))[1]
    assert held == []
    gone = derive(releases, head, "pr", supported=frozenset({"v1.1.0"}))[1]
    assert [(f.rule, f.severity) for f in gone] == [("tombstone-removable", "NOTE")]
    for mode in ("cut", "tag"):
        assert [f.rule for f in derive(releases, head, mode, supported=frozenset({"v1.1.0"}))[1]] == [
            "tombstone-removable"
        ]


@pytest.mark.parametrize("mode", ["pr", "cut", "tag"])
def test_tombstone_removed_fails_while_a_supported_release_holds_it_live(mode: Mode) -> None:
    releases = [snapshot("v1.0.0", on=False), snapshot("v1.1.0", on=False)]
    supported = frozenset({"v1.1.0"})
    findings = derive(releases, registry(), mode, supported=supported)[1]
    assert [(f.rule, f.severity) for f in findings] == [("tombstone-removed", "FAIL")]
    assert derive(releases, registry(retired=True), mode, supported=supported)[1] == []
    assert derive(releases, registry(), mode, supported=frozenset({"v0.9.0"}))[1] == []


def test_flip_without_note_is_judged_in_pr_mode_only() -> None:
    off, on = registry(on=False), registry(on=True)
    assert [(f.rule, f.severity) for f in derive([], on, "pr", base=off)[1]] == [("flip-without-note", "FAIL")]
    assert derive([], on, "pr", base=off, noted=frozenset({"alpha"}))[1] == []
    assert derive([], on, "pr", base=None)[1] == []
    assert derive([], on, "pr", base=on)[1] == []
    assert derive([], off, "pr", base=off)[1] == []
    for mode in ("cut", "tag"):
        assert derive([], on, mode, base=off)[1] == []


def test_workflow_flag_gate() -> None:
    workflow = yaml.safe_load((repo_root() / ".github/workflows/gate.yml").read_text())
    job = workflow["jobs"]["repo-checks"]
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    (step,) = [step for step in job["steps"] if step.get("name") == "flag lifecycle (cut on a release cut, else pr)"]
    assert "github.event_name == 'pull_request'" in step["if"]
    detect, run = step["run"].splitlines()
    assert step["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    assert './scripts/release-cut-in-range.sh "$BASE_SHA" "$HEAD_SHA"' in detect
    assert 'mode="cut"; else mode="pr"' in detect
    assert run.strip() == 'uv run blizzard-flag-lifecycle "$mode" --base "$BASE_SHA"'
