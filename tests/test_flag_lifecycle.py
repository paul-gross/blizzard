"""Release-snapshot lifecycle rules and the installed Git-facing command."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from blizzard.foundation.flags.registry import FlagRegistry
from blizzard.tools.compat.releases import parse_release
from blizzard.tools.flag_lifecycle import main
from blizzard.tools.flag_lifecycle_domain import Snapshot, derive

_PATH = "src/blizzard/foundation/flags/registry.toml"


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


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["pr", "cut", "tag"])
def test_initial_on_and_stable_reversal_fail(mode: str) -> None:
    _, findings = derive([snapshot("v1.0.0-rc.1", on=True), snapshot("v1.0.0", on=True)], registry(on=False), mode)  # type: ignore[arg-type]
    assert [(f.rule, f.severity) for f in findings] == [("initial-on", "FAIL"), ("post-stable-reversal", "FAIL")]


@pytest.mark.unit
@pytest.mark.parametrize("mode,expected", [("pr", "WARN"), ("cut", "FAIL"), ("tag", "FAIL")])
def test_due_and_on_live(mode: str, expected: str) -> None:
    releases = [snapshot("v1.0.0-rc.1", on=False), snapshot("v1.0.0", on=True), snapshot("v1.1.0", on=True)]
    rows, findings = derive(releases, registry(on=True), mode)  # type: ignore[arg-type]
    assert rows[0].due
    assert [(f.rule, f.severity) for f in findings] == [("due-live", expected)]


@pytest.mark.unit
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


@pytest.mark.unit
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


@pytest.mark.unit
def test_head_new_on_and_no_registry_history() -> None:
    assert derive([snapshot("v1.0.0")], registry(on=True), "pr")[1][0].rule == "initial-on"
    for mode in ("pr", "cut", "tag"):
        assert derive([], registry(on=True), mode)[1] == []


def flag_toml(name: str, *, on: bool) -> str:
    return (
        f'[flag.{name}]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "{"on" if on else "off"}"\n'
    )


@pytest.mark.unit
def test_tombstone_removable_once_no_supported_release_holds_it() -> None:
    releases = [snapshot("v1.0.0", on=False), snapshot("v1.1.0", retired=True)]
    head = registry(retired=True)
    held = derive(releases, head, "pr", supported=frozenset({"v1.0.0", "v1.1.0"}))[1]
    assert held == []
    gone = derive(releases, head, "pr", supported=frozenset({"v1.1.0"}))[1]
    assert [(f.rule, f.severity) for f in gone] == [("tombstone-removable", "NOTE")]
    for mode in ("cut", "tag"):
        assert [f.rule for f in derive(releases, head, mode, supported=frozenset({"v1.1.0"}))[1]] == [  # type: ignore[arg-type]
            "tombstone-removable"
        ]


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["pr", "cut", "tag"])
def test_tombstone_removed_fails_while_a_supported_release_holds_it_live(mode: str) -> None:
    releases = [snapshot("v1.0.0", on=False), snapshot("v1.1.0", on=False)]
    supported = frozenset({"v1.1.0"})
    findings = derive(releases, registry(), mode, supported=supported)[1]  # type: ignore[arg-type]
    assert [(f.rule, f.severity) for f in findings] == [("tombstone-removed", "FAIL")]
    assert derive(releases, registry(retired=True), mode, supported=supported)[1] == []  # type: ignore[arg-type]
    assert derive(releases, registry(), mode, supported=frozenset({"v0.9.0"}))[1] == []  # type: ignore[arg-type]


@pytest.mark.unit
def test_flip_without_note_is_judged_in_pr_mode_only() -> None:
    off, on = registry(on=False), registry(on=True)
    assert [(f.rule, f.severity) for f in derive([], on, "pr", base=off)[1]] == [("flip-without-note", "FAIL")]
    assert derive([], on, "pr", base=off, noted=frozenset({"alpha"}))[1] == []
    assert derive([], on, "pr", base=None)[1] == []
    assert derive([], on, "pr", base=on)[1] == []
    assert derive([], off, "pr", base=off)[1] == []
    for mode in ("cut", "tag"):
        assert derive([], on, mode, base=off)[1] == []  # type: ignore[arg-type]


def stub_compat(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tags: list[str], *, exit_code: int = 0) -> Path:
    """Put a ``blizzard-compat`` first on ``PATH`` that answers ``releases --json`` with ``tags``; returns its call log."""
    bin_dir = tmp_path.parent / f"{tmp_path.name}-bin"
    bin_dir.mkdir(exist_ok=True)
    log = bin_dir / "calls"
    document = json.dumps({"releases": [{"tag": tag} for tag in tags]})
    script = bin_dir / "blizzard-compat"
    script.write_text(f"#!/bin/sh\necho \"$@\" >> '{log}'\necho '{document}'\nexit {exit_code}\n")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")
    return log


def init_repo(root: Path) -> None:
    git(root, "init")
    git(root, "config", "user.name", "Test")
    git(root, "config", "user.email", "test@example.com")


@pytest.mark.component
def test_command_judges_tombstones_against_supported_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    save(tmp_path, flag_toml("alpha", on=False), "v1.0.0")
    save(tmp_path, "[retired]\nalpha = 'hub'\n", "v1.1.0")
    monkeypatch.chdir(tmp_path)
    stub_compat(tmp_path, monkeypatch, ["v1.0.0", "v1.1.0"])
    result = CliRunner().invoke(main, ["tag"])
    assert result.exit_code == 0, result.output
    assert "NOTE" not in result.output
    stub_compat(tmp_path, monkeypatch, ["v1.1.0"])
    result = CliRunner().invoke(main, ["tag"])
    assert result.exit_code == 0, result.output
    assert "NOTE [tombstone-removable] alpha" in result.output
    save(tmp_path, "")
    stub_compat(tmp_path, monkeypatch, ["v1.0.0", "v1.1.0"])
    for mode in ("pr", "cut", "tag"):
        result = CliRunner().invoke(main, [mode])
        assert result.exit_code == 1, result.output
        assert "FAIL [tombstone-removed] alpha" in result.output


@pytest.mark.component
def test_command_judges_a_flip_against_base_and_unreleased_notes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    save(tmp_path, flag_toml("alpha", on=False), "v1.0.0")
    git(tmp_path, "branch", "base")
    save(tmp_path, flag_toml("alpha", on=True))
    monkeypatch.chdir(tmp_path)
    stub_compat(tmp_path, monkeypatch, ["v1.0.0"])
    result = CliRunner().invoke(main, ["pr", "--base", "base"])
    assert result.exit_code == 1, result.output
    assert "FAIL [flip-without-note] alpha" in result.output
    assert CliRunner().invoke(main, ["cut", "--base", "base"]).exit_code == 0
    assert CliRunner().invoke(main, ["pr"]).exit_code == 0
    note = tmp_path / "changes" / "unreleased" / "01ABC.md"
    note.parent.mkdir(parents=True)
    note.write_text("---\nkind: feature\nflag: alpha\n---\nTurn alpha on.\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "note")
    result = CliRunner().invoke(main, ["pr", "--base", "base"])
    assert result.exit_code == 0, result.output
    assert "flip-without-note" not in result.output


@pytest.mark.component
def test_command_fails_by_name_when_compat_cannot_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    save(tmp_path, flag_toml("alpha", on=False), "v1.0.0")
    monkeypatch.chdir(tmp_path)
    stub_compat(tmp_path, monkeypatch, [], exit_code=1)
    result = CliRunner().invoke(main)
    assert result.exit_code == 1
    assert "blizzard-compat releases --json failed" in result.output


@pytest.mark.component
def test_command_does_not_consult_compat_before_any_registry_is_tagged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    save(tmp_path, "")
    monkeypatch.chdir(tmp_path)
    log = stub_compat(tmp_path, monkeypatch, [], exit_code=1)
    assert CliRunner().invoke(main, ["tag"]).exit_code == 0
    assert not log.exists()


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def save(root: Path, text: str, tag: str | None = None) -> None:
    file = root / _PATH
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text)
    git(root, "add", ".")
    git(root, "commit", "-m", "snapshot")
    if tag:
        git(root, "tag", tag)


@pytest.mark.component
def test_command_walks_tags_and_reports_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "README").write_text("pre-registry")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "old")
    git(tmp_path, "tag", "v0.9.0")
    save(tmp_path, "")
    git(tmp_path, "tag", "v1.0.0-rc.1")
    git(tmp_path, "tag", "v1.0.0")
    save(
        tmp_path,
        '[flag.alpha]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "off"\n',
        "v1.1.0-rc.1",
    )
    save(tmp_path, '[flag.alpha]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "on"\n', "v1.1.0")
    monkeypatch.chdir(tmp_path)
    stub_compat(tmp_path, monkeypatch, ["v1.0.0-rc.1", "v1.0.0", "v1.1.0-rc.1", "v1.1.0", "v1.2.0", "v1.3.0"])
    result = CliRunner().invoke(main)
    assert result.exit_code == 0, result.output
    assert "alpha: born in=v1.1.0-rc.1; on in=v1.1.0; due=no; retired in=-; off for=1" in result.output
    assert "WARN [on-live]" in result.output
    git(tmp_path, "tag", "v1.2.0")
    for mode, exit_code in [("pr", 0), ("cut", 1), ("tag", 1)]:
        result = CliRunner().invoke(main, [mode])
        assert result.exit_code == exit_code, result.output
        assert "due=yes" in result.output
        assert f"{'WARN' if mode == 'pr' else 'FAIL'} [due-live]" in result.output
    save(tmp_path, "[retired]\nalpha = 'hub'\n", "v1.3.0")
    save(tmp_path, "")
    save(tmp_path, '[flag.alpha]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "off"\n')
    result = CliRunner().invoke(main)
    assert result.exit_code == 1
    assert "retired in=v1.3.0" in result.output
    assert "FAIL [retired-name-reuse]" in result.output


@pytest.mark.component
def test_no_tagged_registry_and_malformed_tag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "user.email", "test@example.com")
    (tmp_path / "README").write_text("old")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-m", "old")
    git(tmp_path, "tag", "v0.9.0")
    save(tmp_path, "")
    monkeypatch.chdir(tmp_path)
    for mode in ("pr", "cut", "tag"):
        assert CliRunner().invoke(main, [mode]).exit_code == 0
    save(tmp_path, "not = [", "v1.0.0")
    save(tmp_path, "")
    result = CliRunner().invoke(main)
    assert result.exit_code != 0
    assert "v1.0.0:" in result.output


@pytest.mark.unit
def test_workflow_flag_gate() -> None:
    workflow = yaml.safe_load((Path(__file__).resolve().parents[1] / ".github/workflows/gate.yml").read_text())
    job = workflow["jobs"]["flag-lifecycle"]
    assert job["name"] == "flag lifecycle"
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["steps"][0]["with"]["fetch-depth"] == 0
    detect, run = job["steps"][-1]["run"].splitlines()
    assert job["steps"][-1]["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    assert './scripts/release-cut-in-range.sh "$BASE_SHA" "$HEAD_SHA"' in detect
    assert 'mode="cut"; else mode="pr"' in detect
    assert run.strip() == 'uv run blizzard-flag-lifecycle "$mode" --base "$BASE_SHA"'
