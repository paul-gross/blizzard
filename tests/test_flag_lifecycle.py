"""Release-snapshot lifecycle rules and the installed Git-facing command."""

from __future__ import annotations

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
        [*releases, snapshot("v1.12.0", retired=True)], registry(retired=True), "pr"
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
    assert job["steps"][-1]["run"] == "uv run blizzard-flag-lifecycle pr"
