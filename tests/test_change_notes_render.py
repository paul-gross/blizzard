"""blizzard:unit-test for ``blizzard-change-notes render`` — the pure renderer over in-memory notes, the command over a real repository."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.cli import main
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.render import render
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_COMMITS = "## Fixes\n\n- a thing (abc1234)"


def _note(note_id: str, body: str, **fields: object) -> Note:
    return Note(path=f"changes/0.3.0/{note_id}.md", directory="0.3.0", id=note_id, fields=fields, body=body)


def test_render_orders_sections_omits_empty_ones_and_ends_in_the_commit_block() -> None:
    notes = [
        _note("z-fix", "Fixes z.\n", kind="fixed"),
        _note("a-fix", "Fixes a.\n", kind="fixed"),
        _note(
            "brk",
            "Drops it. Really.\n\n## Upgrade\n\nSwitch over.\n",
            kind="changed",
            breaking=True,
            **{"upgrade-guide": "drop"},
        ),
        _note("old", "Old thing is deprecated.\n", kind="deprecated", **{"removes-in": "2"}),
        _note("gone", "Removes gone.\n\n## Upgrade\n\nMove.\n", kind="removed"),
        _note("new", "Adds new.\n\nSecond paragraph.\n", kind="added"),
    ]
    expected = """\
## Upgrade notes

### [Drops it.](https://github.com/paul-gross/blizzard/blob/v0.3.0/docs/upgrade.md#drop)

Switch over.

### Removes gone.

Move.

## Breaking changes

- Drops it. Really.

## Deprecated

- Old thing is deprecated.
  Removal planned for 2.

## Added

- Adds new.

  Second paragraph.

## Changed

- Removes gone.

## Fixed

- Fixes a.
- Fixes z. (also in 0.2.1)

## Commits

<details>
<summary>Commits</summary>

## Fixes

- a thing (abc1234)

</details>
"""
    assert render("v0.3.0", notes, {"z-fix": ["0.2.1"]}, _COMMITS) == expected


def test_render_of_no_notes_is_the_commit_block_alone() -> None:
    assert render("v0.3.0", [], {}, _COMMITS).startswith("## Commits\n")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _fixed(text: str) -> str:
    return f"---\nkind: fixed\n---\n\n{text}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / "scripts").mkdir(parents=True)
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Test")
    _git(path, "config", "user.email", "test@example.com")
    shutil.copy(repo_root() / "scripts" / "release-notes.sh", path / "scripts" / "release-notes.sh")
    _commit(path, "feat: first", {"changes/0.1.0/one.md": _fixed("Fixes one."), "README.md": "x"})
    _git(path, "tag", "v0.1.0")
    _commit(
        path,
        "fix: second",
        {"changes/0.2.0/two.md": _fixed("Fixes two."), "changes/0.2.0/shared.md": _fixed("Shared.")},
    )
    _git(path, "tag", "v0.2.0")
    _git(path, "checkout", "-q", "-b", "release/0.1", "v0.1.0")
    _commit(path, "fix: patch", {"changes/0.1.1/shared.md": _fixed("Shared.")})
    _git(path, "tag", "v0.1.1")
    _git(path, "checkout", "-q", "main")
    return path


def _render(repo: Path, tag: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(repo)
    return CliRunner().invoke(main, ["render", tag])


def test_render_of_a_two_tag_repository_is_golden_and_repeatable(repo: Path, monkeypatch) -> None:
    first = _render(repo, "v0.2.0", monkeypatch)
    assert first.exit_code == 0, first.output
    expected = """\
## Fixed

- Shared. (also in 0.1.1)
- Fixes two.

## Commits

<details>
<summary>Commits</summary>

## Upgrade notes

_Fill in when this release asks something of the operator — see docs/versioning.md._

## Fixes

- second (SHA)

</details>
"""
    sha = _git(repo, "log", "-1", "--format=%h", "v0.2.0")
    assert first.output == expected.replace("SHA", sha)
    assert _render(repo, "v0.2.0", monkeypatch).output == first.output


def test_a_tag_without_a_notes_directory_renders_the_commit_block_alone(repo: Path, monkeypatch) -> None:
    _commit(repo, "chore: later", {"x": "y"})
    _git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("## Commits\n") and "- later (" in result.output


def test_a_tag_with_no_previous_tag_renders_the_empty_range_block(repo: Path, monkeypatch) -> None:
    result = _render(repo, "v0.1.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "- Fixes one." in result.output
    assert "- first (" not in result.output


def test_a_note_that_breaks_the_format_fails_naming_it(repo: Path, monkeypatch) -> None:
    _commit(repo, "fix: bad", {"changes/0.3.0/bad.md": "no frontmatter\n"})
    _git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 1
    assert "changes/0.3.0/bad.md" in result.output


def test_a_non_release_tag_is_a_usage_error(repo: Path, monkeypatch) -> None:
    assert _render(repo, "nightly", monkeypatch).exit_code == 2


def test_turning_on_flags_reads_each_tags_registry_and_omits_empty_section(repo: Path, monkeypatch) -> None:
    path = "src/blizzard/foundation/flags/registry.toml"
    off = '[flag.alpha]\ndefault = "off"\nsummary = "Alpha capability"\n'
    _commit(repo, "feat: staged", {path: off})
    _git(repo, "tag", "v0.3.0")
    _commit(repo, "feat: enabled", {path: off.replace('"off"', '"on"')})
    _git(repo, "tag", "v0.4.0")
    _commit(repo, "chore: later", {"README.md": "later"})
    _git(repo, "tag", "v0.5.0")
    turned = _render(repo, "v0.4.0", monkeypatch)
    assert turned.exit_code == 0, turned.output
    assert turned.output.startswith("## Turned on\n\n- Alpha capability\n")
    assert "## Turned on" not in _render(repo, "v0.3.0", monkeypatch).output
    assert "## Turned on" not in _render(repo, "v0.5.0", monkeypatch).output
    assert "## Turned on" not in _render(repo, "v0.2.0", monkeypatch).output


def test_a_flag_added_on_after_a_tag_without_a_registry_is_listed(repo: Path, monkeypatch) -> None:
    _commit(
        repo,
        "feat: immediate capability",
        {
            "src/blizzard/foundation/flags/registry.toml": '[flag.immediate]\ndefault = "on"\nsummary = "Immediate use"\n'
        },
    )
    _git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "## Turned on\n\n- Immediate use" in result.output
