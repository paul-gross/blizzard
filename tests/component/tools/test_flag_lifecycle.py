"""The installed Git-facing release-snapshot lifecycle command."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.flag_lifecycle import main

_PATH = "src/blizzard/foundation/flags/registry.toml"


def flag_toml(name: str, *, on: bool) -> str:
    return (
        f'[flag.{name}]\nowner = "epic:flags"\nsummary = "A flag"\nscope = "hub"\ndefault = "{"on" if on else "off"}"\n'
    )


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


def test_command_fails_by_name_when_compat_cannot_resolve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_repo(tmp_path)
    save(tmp_path, flag_toml("alpha", on=False), "v1.0.0")
    monkeypatch.chdir(tmp_path)
    stub_compat(tmp_path, monkeypatch, [], exit_code=1)
    result = CliRunner().invoke(main)
    assert result.exit_code == 1
    assert "blizzard-compat releases --json failed" in result.output


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
