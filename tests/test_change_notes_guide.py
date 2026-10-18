"""blizzard:unit-test for the line guide over notes and tagged history."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.cli import main
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.guide import due_without_removal, guide
from blizzard.tools.change_notes.render import render
from tests.repo_files import repo_root
from tests.test_change_notes_render import _commit, _git

pytestmark = pytest.mark.unit


def _note(version: str, note_id: str, kind: str, **fields: object) -> Note:
    return Note(f"changes/{version}/{note_id}.md", version, note_id, {"kind": kind, **fields}, f"{note_id} prose.\n")


def _file(kind: str, body: str, **fields: str) -> str:
    front = "".join(f'{key}: "{value}"\n' for key, value in fields.items())
    return f"---\nkind: {kind}\n{front}---\n\n{body}\n"


def test_guide_uses_numeric_history_latest_postponement_and_only_current_removal() -> None:
    notes = [
        _note("1.10.0", "delay", "changed", postpones="old", **{"removes-in": "4"}),
        _note("1.9.0", "old", "deprecated", **{"removes-in": "2"}),
        _note("3.0.0", "premature", "removed", retires="old"),
        _note("4.0.0", "gone", "removed", retires="old"),
        _note("4.0.0", "direct", "changed", breaking=True),
    ]
    assert due_without_removal(notes, "4") == ()
    text = guide(reversed(notes), "4", tag="v4.0.0")
    assert text.startswith("## Upgrading to 4\n")
    assert text.index("old prose") < text.index("gone prose") < text.index("direct prose")
    assert "premature prose" not in text
    assert guide(notes, "2") == ""
    assert due_without_removal(notes[:-2], "4") == ("old",)


def test_pre_one_guide_orders_deprecations_by_numeric_release_and_links_boundaries() -> None:
    notes = [
        _note("0.10.0", "a", "deprecated", **{"removes-in": "0.12"}),
        _note("0.9.0", "z", "deprecated", **{"removes-in": "0.12"}),
        _note("0.12.0", "remove-a", "removed", retires="a", **{"upgrade-guide": "steps"}),
        _note("0.12.0", "remove-z", "removed", retires="z"),
    ]
    text = guide(notes, "0.12", tag="v0.12.0")
    assert text.index("z prose") < text.index("a prose")
    assert "blob/v0.12.0/docs/upgrade.md#steps" in text


def test_guide_command_refuses_unretired_and_prints_a_completed_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "changes" / "1.9.0").mkdir(parents=True)
    (tmp_path / "changes" / "1.9.0" / "old.md").write_text(_file("deprecated", "Old API.", **{"removes-in": "2"}))
    monkeypatch.chdir(tmp_path)
    missing = CliRunner().invoke(main, ["guide", "2"])
    assert missing.exit_code == 1
    assert "old" in missing.output
    (tmp_path / "changes" / "2.0.0").mkdir()
    (tmp_path / "changes" / "2.0.0" / "gone.md").write_text(
        _file("removed", "Gone.\n\n## Upgrade\n\nSwitch.", retires="old")
    )
    result = CliRunner().invoke(main, ["guide", "2"])
    assert result.exit_code == 0, result.output
    assert result.output.startswith("## Upgrading to 2\n")
    assert result.output.index("Old API.") < result.output.index("Gone.")


def test_pending_removal_completes_line_without_changing_released_schedule(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "changes" / "1.9.0").mkdir(parents=True)
    (tmp_path / "changes" / "unreleased").mkdir()
    (tmp_path / "changes" / "1.9.0" / "old.md").write_text(_file("deprecated", "Old API.", **{"removes-in": "2"}))
    (tmp_path / "changes" / "unreleased" / "gone.md").write_text(
        _file("removed", "Gone.\n\n## Upgrade\n\nSwitch.", retires="old")
    )
    (tmp_path / "changes" / "unreleased" / "break.md").write_text(
        "---\nkind: changed\nbreaking: true\n---\n\nNew break.\n\n## Upgrade\n\nAdapt.\n"
    )
    before = {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / "changes").rglob("*.md")}
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["guide", "2"])
    assert result.exit_code == 0, result.output
    assert result.output.index("Old API.") < result.output.index("Gone.") < result.output.index("New break.")
    assert {p.relative_to(tmp_path): p.read_bytes() for p in (tmp_path / "changes").rglob("*.md")} == before
    (tmp_path / "changes" / "unreleased" / "delay.md").write_text(
        _file("changed", "Delay.", postpones="old", **{"removes-in": "3"})
    )
    assert "Old API." in CliRunner().invoke(main, ["guide", "2"]).output


def test_guide_refuses_only_when_due_has_neither_released_nor_pending_removal(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "changes" / "1.9.0").mkdir(parents=True)
    (tmp_path / "changes" / "2.1.0").mkdir()
    (tmp_path / "changes" / "unreleased").mkdir()
    for note_id in ("first", "second"):
        (tmp_path / "changes" / "1.9.0" / f"{note_id}.md").write_text(
            _file("deprecated", f"{note_id} API.", **{"removes-in": "2"})
        )
    (tmp_path / "changes" / "2.1.0" / "first-gone.md").write_text(
        _file("removed", "First gone.\n\n## Upgrade\n\nSwitch.", retires="first")
    )
    monkeypatch.chdir(tmp_path)
    missing = CliRunner().invoke(main, ["guide", "2"])
    assert missing.exit_code == 1 and "second" in missing.output and "first" not in missing.output
    (tmp_path / "changes" / "unreleased" / "second-gone.md").write_text(
        _file("removed", "Second gone.\n\n## Upgrade\n\nSwitch.", retires="second")
    )
    result = CliRunner().invoke(main, ["guide", "2"])
    assert result.exit_code == 0, result.output
    assert "First gone." in result.output and "Second gone." in result.output


def test_legacy_removal_without_breaking_flag_or_matching_deprecation_still_appears() -> None:
    notes = [_note("2.0.0", "legacy", "removed", retires="old")]
    assert "legacy prose" in guide(notes, "2")
    assert "legacy prose" not in guide(notes, "3")


def test_earlier_retirement_does_not_block_later_line() -> None:
    notes = [
        _note("0.9.0", "old", "deprecated", **{"removes-in": "0.12"}),
        _note("0.12.0", "gone", "removed", retires="old"),
        _note("0.12.0", "next", "deprecated", **{"removes-in": "1"}),
    ]
    assert due_without_removal(notes, "1") == ("next",)


def test_pending_postponement_does_not_change_due_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "changes" / "1.9.0").mkdir(parents=True)
    (tmp_path / "changes" / "unreleased").mkdir()
    (tmp_path / "changes" / "1.9.0" / "old.md").write_text(_file("deprecated", "Old.", **{"removes-in": "2"}))
    (tmp_path / "changes" / "unreleased" / "later.md").write_text(
        _file("changed", "Later.", postpones="old", **{"removes-in": "3"})
    )
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(main, ["guide", "2"])
    assert result.exit_code == 1 and "old" in result.output


def test_tagged_render_sees_history_at_tag_not_working_tree_and_candidate_is_repeatable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "scripts").mkdir()
    shutil.copy(repo_root() / "scripts" / "release-notes.sh", repo / "scripts" / "release-notes.sh")
    _commit(repo, "feat: deprecate", {"changes/1.9.0/old.md": _file("deprecated", "Old API.", **{"removes-in": "2"})})
    _git(repo, "tag", "v1.9.0")
    _commit(
        repo,
        "feat: postpone",
        {"changes/1.10.0/delay.md": _file("changed", "Later.", postpones="old", **{"removes-in": "3"})},
    )
    _git(repo, "tag", "v1.10.0")
    _commit(repo, "feat: new major", {"changes/2.0.0/new.md": _file("fixed", "New.")})
    _git(repo, "tag", "v2.0.0-rc.1")
    _commit(
        repo,
        "feat: removal",
        {"changes/3.0.0/gone.md": _file("removed", "Gone.\n\n## Upgrade\n\nSwitch.", retires="old")},
    )
    _git(repo, "tag", "v3.0.0-rc.1")
    monkeypatch.chdir(repo)
    candidate = CliRunner().invoke(main, ["render", "v2.0.0-rc.1"])
    assert candidate.exit_code == 0, candidate.output
    assert candidate.output.startswith("## Upgrading to 2\n")
    first = CliRunner().invoke(main, ["render", "v3.0.0-rc.1"])
    assert first.exit_code == 0, first.output
    assert first.output.startswith("## Upgrading to 3\n")
    assert first.output.index("Old API.") < first.output.index("Gone.") < first.output.index("## Upgrade notes")
    postponed = CliRunner().invoke(main, ["render", "v1.9.0"])
    assert "Removal planned for 2." in postponed.output
    assert "Removal planned for 3." in render(
        "v3.0.0", [_note("1.9.0", "old", "deprecated", **{"removes-in": "2"})], {}, "", schedule={"old": "3"}
    )
    assert CliRunner().invoke(main, ["render", "v3.0.0-rc.1"]).output == first.output
    assert subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True).stdout == ""


def test_tagged_opening_does_not_borrow_pending_removal_from_tag_or_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "scripts").mkdir()
    shutil.copy(repo_root() / "scripts" / "release-notes.sh", repo / "scripts" / "release-notes.sh")
    _commit(repo, "feat: deprecate", {"changes/1.9.0/old.md": _file("deprecated", "Old.", **{"removes-in": "2"})})
    _git(repo, "tag", "v1.9.0")
    _commit(
        repo,
        "feat: candidate",
        {
            "changes/2.0.0/new.md": _file("fixed", "New."),
            "changes/unreleased/gone.md": _file("removed", "Gone.\n\n## Upgrade\n\nSwitch.", retires="old"),
        },
    )
    _git(repo, "tag", "v2.0.0")
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["render", "v2.0.0"])
    assert result.exit_code == 1 and "old" in result.output
    assert "Gone." in CliRunner().invoke(main, ["guide", "2"]).output
