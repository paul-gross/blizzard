"""blizzard:unit-test for ``blizzard-change-notes render`` — the pure renderer over in-memory notes, the command over a real repository."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, TypedDict

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.breaks import BreakEntry, NamedBreak
from blizzard.tools.change_notes.cli import main
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.render import render
from tests.compat_stub import entry, stub_compat
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_COMMITS = "## Fixes\n\n- a thing (abc1234)"


_FrontMatter = TypedDict(
    "_FrontMatter",
    {"kind": str, "breaking": bool, "upgrade-guide": str, "removes-in": str},
    total=False,
)


def _note(note_id: str, body: str, fields: _FrontMatter) -> Note:
    return Note(path=f"changes/0.3.0/{note_id}.md", directory="0.3.0", id=note_id, fields=fields, body=body)


def test_render_orders_sections_omits_empty_ones_and_ends_in_the_commit_block() -> None:
    notes = [
        _note("z-fix", "Fixes z.\n", {"kind": "fixed"}),
        _note("a-fix", "Fixes a.\n", {"kind": "fixed"}),
        _note(
            "brk",
            "Drops it. Really.\n\n## Upgrade\n\nSwitch over.\n",
            {"kind": "changed", "breaking": True, "upgrade-guide": "drop"},
        ),
        _note("old", "Old thing is deprecated.\n", {"kind": "deprecated", "removes-in": "2"}),
        _note("gone", "Removes gone.\n\n## Upgrade\n\nMove.\n", {"kind": "removed"}),
        _note("new", "Adds new.\n\nSecond paragraph.\n", {"kind": "added"}),
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


def test_render_lists_each_recorded_break_beside_its_note_at_the_end_of_upgrade_notes() -> None:
    notes = [
        _note("brk", "Drops it. Really.\n\n## Upgrade\n\nSwitch.\n", {"kind": "changed", "breaking": True}),
        _note("route", "Drops the route. Yes.\n", {"kind": "changed", "breaking": True, "upgrade-guide": "route"}),
    ]
    named = (NamedBreak(BreakEntry("v0.2.0", "http-api", "GET /old"), notes[1]),)
    text = render("v0.3.0", notes, {}, _COMMITS, breaks=named)
    assert text.startswith(
        "## Upgrade notes\n\n### Drops it.\n\nSwitch.\n\n"
        "### [Drops the route.](https://github.com/paul-gross/blizzard/blob/v0.3.0/docs/upgrade.md#route)\n\n"
        "Breaks GET /old (http-api) in v0.2.0.\n\n## Breaking changes"
    )


def test_render_of_only_a_recorded_break_still_has_an_upgrade_notes_section() -> None:
    note = _note("brk", "Drops it.\n", {"kind": "changed", "breaking": True})
    named = (NamedBreak(BreakEntry("v0.2.0", "http-api", "GET /old"), note),)
    assert render("v0.3.0", [note], {}, _COMMITS, breaks=named).startswith("## Upgrade notes\n\n### Drops it.")


def test_render_of_no_notes_is_the_commit_block_alone() -> None:
    assert render("v0.3.0", [], {}, _COMMITS).startswith("## Commits\n")


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for name, text in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)


def _fixed(text: str) -> str:
    return f"---\nkind: fixed\n---\n\n{text}\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    (path / "scripts").mkdir(parents=True)
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.com")
    shutil.copy(repo_root() / "scripts" / "release-notes.sh", path / "scripts" / "release-notes.sh")
    commit(path, "feat: first", {"changes/0.1.0/one.md": _fixed("Fixes one."), "README.md": "x"})
    git(path, "tag", "v0.1.0")
    commit(
        path,
        "fix: second",
        {"changes/0.2.0/two.md": _fixed("Fixes two."), "changes/0.2.0/shared.md": _fixed("Shared.")},
    )
    git(path, "tag", "v0.2.0")
    git(path, "checkout", "-q", "-b", "release/0.1", "v0.1.0")
    commit(path, "fix: patch", {"changes/0.1.1/shared.md": _fixed("Shared.")})
    git(path, "tag", "v0.1.1")
    git(path, "checkout", "-q", "main")
    return path


def _render(
    repo: Path, tag: str, monkeypatch: pytest.MonkeyPatch, *args: str, supported: tuple[str, ...] = ("v0.1.0", "v0.1.1")
):
    log = stub_compat(repo, monkeypatch, supported=supported)
    monkeypatch.chdir(repo)
    result = CliRunner().invoke(main, ["render", tag, *args])
    result.compat_calls = log.read_text() if log.exists() else ""  # type: ignore[attr-defined]
    return result


def test_render_of_a_two_tag_repository_is_golden_and_repeatable(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first = _render(repo, "v0.2.0", monkeypatch)
    assert first.exit_code == 0, first.output
    expected = """\
## Fixed

- Shared. (also in 0.1.1)
- Fixes two.

## Commits

<details>
<summary>Commits</summary>

## Fixes

- second (SHA)

</details>
"""
    sha = git(repo, "log", "-1", "--format=%h", "v0.2.0")
    assert first.output == expected.replace("SHA", sha)
    assert _render(repo, "v0.2.0", monkeypatch).output == first.output


def test_a_tag_without_a_notes_directory_renders_the_commit_block_alone(repo: Path, monkeypatch) -> None:
    commit(repo, "chore: later", {"x": "y"})
    git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert result.output.startswith("## Commits\n") and "- later (" in result.output


def test_a_tag_with_no_previous_tag_renders_the_empty_range_block(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _render(repo, "v0.1.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "- Fixes one." in result.output
    assert "- first (" not in result.output


def test_a_note_that_breaks_the_format_fails_naming_it(repo: Path, monkeypatch) -> None:
    commit(repo, "fix: bad", {"changes/0.3.0/bad.md": "no frontmatter\n"})
    git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 1
    assert "changes/0.3.0/bad.md" in result.output


def test_a_non_release_tag_is_a_usage_error(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _render(repo, "nightly", monkeypatch).exit_code == 2


def test_turning_on_flags_reads_each_tags_registry_and_omits_empty_section(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = "src/blizzard/foundation/flags/registry.toml"
    off = '[flag.alpha]\ndefault = "off"\nsummary = "Alpha capability"\n'
    commit(repo, "feat: staged", {path: off})
    git(repo, "tag", "v0.3.0")
    commit(repo, "feat: enabled", {path: off.replace('"off"', '"on"')})
    git(repo, "tag", "v0.4.0")
    commit(repo, "chore: later", {"README.md": "later"})
    git(repo, "tag", "v0.5.0")
    turned = _render(repo, "v0.4.0", monkeypatch)
    assert turned.exit_code == 0, turned.output
    assert turned.output.startswith("## Turned on\n\n- Alpha capability\n")
    assert "## Turned on" not in _render(repo, "v0.3.0", monkeypatch).output
    assert "## Turned on" not in _render(repo, "v0.5.0", monkeypatch).output
    assert "## Turned on" not in _render(repo, "v0.2.0", monkeypatch).output


def test_a_flag_added_on_after_a_tag_without_a_registry_is_listed(repo: Path, monkeypatch) -> None:
    commit(
        repo,
        "feat: immediate capability",
        {
            "src/blizzard/foundation/flags/registry.toml": '[flag.immediate]\ndefault = "on"\nsummary = "Immediate use"\n'
        },
    )
    git(repo, "tag", "v0.3.0")
    result = _render(repo, "v0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "## Turned on\n\n- Immediate use" in result.output


def test_stable_tag_on_candidates_commit_does_not_repeat_its_flag(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = "src/blizzard/foundation/flags/registry.toml"
    off = '[flag.alpha]\ndefault = "off"\nsummary = "Alpha capability"\n'
    commit(repo, "feat: staged", {path: off})
    git(repo, "tag", "v0.3.0")
    commit(repo, "feat: enabled", {path: off.replace('"off"', '"on"')})
    git(repo, "tag", "v0.4.0-rc.1")
    git(repo, "tag", "v0.4.0")
    candidate = _render(repo, "v0.4.0-rc.1", monkeypatch)
    stable = _render(repo, "v0.4.0", monkeypatch)
    assert candidate.exit_code == stable.exit_code == 0
    assert "## Turned on\n\n- Alpha capability" in candidate.output
    assert "## Turned on" not in stable.output


_BREAKING = "---\nkind: changed\nbreaking: true\nsurfaces: [http-api]\nbreaks: ['GET /old', 'GET /ghost']\n---\n\nDrops it.\n\n## Upgrade\n\nMove.\n"


def _breaks_file(repo: Path, tag: str, *entries: dict[str, Any]) -> str:
    path = repo / "breaks.json"
    path.write_text(json.dumps({"tag": tag, "breaks": list(entries)}))
    return str(path)


@pytest.fixture
def breaking(repo: Path) -> Path:
    commit(repo, "feat: drop", {"changes/0.3.0/drop.md": _BREAKING})
    git(repo, "tag", "v0.3.0")
    return repo


def test_render_with_breaks_lists_the_break_and_reports_an_unrecorded_one(
    breaking: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _breaks_file(breaking, "v0.3.0", entry("v0.2.0", "http-api", "GET /old"))
    result = _render(breaking, "v0.3.0", monkeypatch, "--breaks", path)
    assert result.exit_code == 0, result.output
    assert "### Drops it.\n\nBreaks GET /old (http-api) in v0.2.0.\n" in result.stdout
    assert "GET /ghost is recorded by no compat break" in result.stderr


def test_render_with_breaks_fails_on_an_unnamed_break_with_empty_stdout(
    breaking: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _breaks_file(breaking, "v0.3.0", entry("v0.2.0", "http-api", "GET /other"))
    result = _render(breaking, "v0.3.0", monkeypatch, "--breaks", path)
    assert result.exit_code == 1 and result.stdout == ""
    assert "http-api GET /other (breaks v0.2.0)" in result.stderr


def test_render_refuses_a_breaks_file_judging_another_tag(breaking: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _render(breaking, "v0.3.0", monkeypatch, "--breaks", _breaks_file(breaking, "v0.2.0"))
    assert result.exit_code == 1 and result.stdout == "" and "judged v0.2.0, not v0.3.0" in result.stderr


def test_render_without_breaks_is_unchanged_by_the_option(breaking: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _render(breaking, "v0.3.0", monkeypatch)
    assert plain.exit_code == 0 and "Breaks GET" not in plain.stdout


def test_also_in_marks_only_supported_releases(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _render(repo, "v0.2.0", monkeypatch, supported=("v0.1.0",))
    assert "- Shared.\n" in result.output and "also in" not in result.output
    assert "releases --json --tag 0.2.0" in result.compat_calls  # type: ignore[attr-defined]


def test_a_tag_with_no_notes_consults_no_compat(repo: Path, monkeypatch) -> None:
    commit(repo, "chore: later", {"x": "y"})
    git(repo, "tag", "v0.3.0")
    assert _render(repo, "v0.3.0", monkeypatch).compat_calls == ""  # type: ignore[attr-defined]
