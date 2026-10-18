"""blizzard:unit-test for ``blizzard-change-notes cut`` — fixture trees built under ``tmp_path``."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.cli import main
from blizzard.tools.change_notes.contract import Note
from blizzard.tools.change_notes.schedule import latest_released_schedule, line_order, release_line
from blizzard.tools.change_notes.upgrade_doc import UpgradeDocError, move_boundaries
from blizzard.tools.change_notes.validate import validate

pytestmark = pytest.mark.unit

_DOC = """# Upgrading

## Before you pull

Read this first.

```sh
## not a heading
```

## Upgrading to the next release

### Alpha boundary

Alpha text.

### Beta boundary

Beta text.

## Pull and recreate

Pull.
"""

# The staged "Upgrading to 0.3.0" boundary would share its anchor with the section the cut creates.
_SHIFTED_DOC = _DOC.replace("### Alpha boundary", "### Upgrading to 0.3.0")
_BREAKING = (
    "---\nkind: changed\nbreaking: true\nupgrade-guide: alpha-boundary\n---\n\nChanges alpha.\n\n## Upgrade\n\nDo it.\n"
)
_FIXED = "---\nkind: fixed\n---\n\nFixes beta.\n"


def _shifted_note() -> str:
    return _BREAKING.replace("alpha-boundary", "upgrading-to-030")


def _tree(tmp_path: Path, notes: dict[str, str], doc: str | None = _DOC) -> Path:
    (tmp_path / "changes" / "unreleased").mkdir(parents=True)
    (tmp_path / "changes" / "unreleased" / ".gitkeep").write_text("")
    for rel, text in notes.items():
        target = tmp_path / "changes" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    if doc is not None:
        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "upgrade.md").write_text(doc)
    return tmp_path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


def _cut(root: Path, version: str, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(root)
    return CliRunner().invoke(main, ["cut", version])


def test_cut_moves_every_unreleased_note_and_keeps_the_keepfile(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {"unreleased/b-fix.md": _FIXED, "unreleased/a-fix.md": _FIXED})
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert sorted(p.name for p in (root / "changes" / "0.3.0").iterdir()) == ["a-fix.md", "b-fix.md"]
    assert [p.name for p in (root / "changes" / "unreleased").iterdir()] == [".gitkeep"]
    assert validate(root) == []


def test_a_second_cut_adds_to_the_existing_version_directory(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {"unreleased/one.md": _FIXED})
    assert _cut(root, "0.3.0", monkeypatch).exit_code == 0
    (root / "changes" / "unreleased" / "two.md").write_text(_FIXED)
    assert _cut(root, "0.3.0", monkeypatch).exit_code == 0
    assert sorted(p.name for p in (root / "changes" / "0.3.0").iterdir()) == ["one.md", "two.md"]


def test_a_cut_with_nothing_to_move_succeeds_and_says_so(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {})
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 0
    assert "nothing to cut" in result.output
    assert not (root / "changes" / "0.3.0").exists()


def test_cut_holds_off_flag_then_moves_note_and_guide_when_on(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {"unreleased/alpha.md": _BREAKING.replace("breaking: true", "breaking: true\nflag: staged")})
    registry = root / "src/blizzard/foundation/flags/registry.toml"
    registry.parent.mkdir(parents=True)
    registry.write_text('[flag.staged]\ndefault = "off"\nsummary = "Staged capability"\n')
    before = (root / "docs/upgrade.md").read_text()
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "held alpha" in result.output
    assert "all unreleased notes are held" in result.output
    assert (root / "changes/unreleased/alpha.md").is_file()
    assert (root / "docs/upgrade.md").read_text() == before
    registry.write_text(registry.read_text().replace('"off"', '"on"'))
    result = _cut(root, "0.4.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "moved alpha" in result.output
    assert (root / "changes/0.4.0/alpha.md").is_file()
    assert "## Upgrading to 0.4.0" in (root / "docs/upgrade.md").read_text()


def test_cut_refuses_to_move_a_held_nested_guide_with_its_parent(tmp_path: Path, monkeypatch) -> None:
    doc = _DOC.replace("Alpha text.", "Alpha text.\n\n#### Inner boundary\n\nInner text.")
    held = _BREAKING.replace("alpha-boundary", "inner-boundary").replace(
        "breaking: true", "breaking: true\nflag: staged"
    )
    root = _tree(tmp_path, {"unreleased/outer.md": _BREAKING, "unreleased/inner.md": held}, doc)
    registry = root / "src/blizzard/foundation/flags/registry.toml"
    registry.parent.mkdir(parents=True)
    registry.write_text('[flag.staged]\ndefault = "off"\nsummary = "Staged capability"\n')
    before = _snapshot(root)
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 1
    assert "inner-boundary" in result.output
    assert _snapshot(root) == before


def test_cut_moves_a_named_boundary_into_a_new_section_beneath_before_you_pull(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {"unreleased/alpha.md": _BREAKING})
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    doc = (root / "docs" / "upgrade.md").read_text()
    assert doc.index("## Before you pull") < doc.index("## Upgrading to 0.3.0") < doc.index("### Alpha boundary")
    assert doc.index("### Alpha boundary") < doc.index("## Upgrading to the next release")
    assert "### Beta boundary" in doc.split("## Upgrading to the next release")[1]
    assert "## Upgrading to the next release" in doc
    assert "not a heading" in doc.split("## Upgrading to 0.3.0")[0]
    assert validate(root) == []


def test_cut_appends_a_boundary_to_an_existing_release_section(tmp_path: Path, monkeypatch) -> None:
    doc = _DOC.replace(
        "## Upgrading to the next release",
        "## Upgrading to 0.3.0\n\n### Old boundary\n\nOld.\n\n## Upgrading to the next release",
    )
    root = _tree(tmp_path, {"unreleased/alpha.md": _BREAKING}, doc)
    assert _cut(root, "0.3.0", monkeypatch).exit_code == 0
    text = (root / "docs" / "upgrade.md").read_text()
    assert text.count("## Upgrading to 0.3.0") == 1
    assert (
        text.index("### Old boundary")
        < text.index("### Alpha boundary")
        < text.index("## Upgrading to the next release")
    )
    assert validate(root) == []


def test_a_boundary_already_under_a_release_heading_is_left_and_named(tmp_path: Path, monkeypatch) -> None:
    doc = _DOC.replace(
        "## Upgrading to the next release",
        "## Upgrading to 0.2.0\n\n### Alpha boundary\n\nOld.\n\n## Upgrading to the next release",
    )
    root = _tree(tmp_path, {"unreleased/alpha.md": _BREAKING}, doc)
    result = _cut(root, "0.3.0", monkeypatch)
    assert result.exit_code == 0, result.output
    assert "left upgrade boundary alpha-boundary" in result.output
    assert "## Upgrading to 0.3.0" not in (root / "docs" / "upgrade.md").read_text()


@pytest.mark.parametrize(
    ("notes", "doc", "version"),
    [
        ({"unreleased/bad.md": "no frontmatter\n"}, _DOC, "0.3.0"),
        ({"unreleased/ok.md": _FIXED}, _DOC, "0.3"),
        ({"unreleased/alpha.md": _BREAKING}, _DOC.replace("## Before you pull", "## Prelude"), "0.3.0"),
        ({"unreleased/alpha.md": _shifted_note()}, _SHIFTED_DOC, "0.3.0"),
    ],
)
def test_a_refused_cut_exits_one_and_leaves_the_tree_untouched(
    tmp_path: Path, monkeypatch, notes, doc, version
) -> None:
    root = _tree(tmp_path, notes, doc)
    before = _snapshot(root)
    assert _cut(root, version, monkeypatch).exit_code == 1
    assert _snapshot(root) == before


def test_move_boundaries_without_a_staging_heading_returns_the_text(tmp_path: Path) -> None:
    text = "## Before you pull\n\nx\n"
    assert move_boundaries(text, "0.3.0", ["a"]).text == text


def test_move_boundaries_needs_before_you_pull_for_a_new_section() -> None:
    with pytest.raises(UpgradeDocError):
        move_boundaries("## Upgrading to the next release\n\n### A\n\nx\n", "0.3.0", ["a"])


def test_a_wanted_boundary_nested_in_another_wanted_one_counts_as_moved() -> None:
    text = "## Before you pull\n\nx\n\n## Upgrading to the next release\n\n### Outer\n\no\n\n#### Inner\n\ni\n"
    moved = move_boundaries(text, "0.3.0", ["outer", "inner"])
    assert moved.anchors == ("outer", "inner")
    assert moved.text.index("## Upgrading to 0.3.0") < moved.text.index("#### Inner")


def _scheduled(kind: str, line: str, *, target: str | None = None) -> str:
    reference = f"postpones: {target}\n" if target else ""
    return f'---\nkind: {kind}\n{reference}removes-in: "{line}"\n---\n\nChanges the schedule.\n'


_REMOVAL = "---\nkind: removed\nbreaking: true\nretires: old\n---\n\nRemoves old.\n\n## Upgrade\n\nReplace it.\n"


@pytest.mark.parametrize(
    ("version", "other", "accepted"),
    [
        ("0.12.0", {}, False),
        ("0.12.2", {"unreleased/removal.md": _REMOVAL}, True),
        ("0.12.2", {"0.12.0/removal.md": _REMOVAL}, True),
        ("0.12.2", {"unreleased/delay.md": _scheduled("changed", "0.13", target="old")}, True),
        ("0.11.4", {}, True),
    ],
)
def test_due_pre_one_deprecation_needs_removal_or_postponement_before_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, other: dict[str, str], accepted: bool
) -> None:
    root = _tree(tmp_path, {"0.10.0/old.md": _scheduled("deprecated", "0.12"), **other})
    before = _snapshot(root)
    result = _cut(root, version, monkeypatch)
    assert (result.exit_code == 0) is accepted, result.output
    if not accepted:
        assert "deprecation old is due in 0.12" in result.output
        assert _snapshot(root) == before


def test_due_major_deprecation_refuses_an_empty_cut_without_writes(tmp_path: Path, monkeypatch) -> None:
    root = _tree(tmp_path, {"1.9.0/old.md": _scheduled("deprecated", "2")})
    before = _snapshot(root)
    result = _cut(root, "2.0.0", monkeypatch)
    assert result.exit_code == 1
    assert "deprecation old is due in 2" in result.output
    assert _snapshot(root) == before


@pytest.mark.parametrize(
    ("version", "line", "accepted"),
    [
        ("0.12.0", "0.12", False),
        ("0.12.4", "0.13", True),
        ("0.12.4", "0.11", False),
        ("0.12.4", "2", False),
        ("1.8.2", "1", False),
        ("1.8.2", "2", True),
        ("1.8.2", "0.12", False),
        ("2.0.1", "2", False),
        ("2.0.1", "3", True),
    ],
)
def test_new_deprecation_must_name_a_later_line_in_the_cut_vocabulary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: str, line: str, accepted: bool
) -> None:
    root = _tree(tmp_path, {"unreleased/new.md": _scheduled("deprecated", line)})
    before = _snapshot(root)
    result = _cut(root, version, monkeypatch)
    assert (result.exit_code == 0) is accepted, result.output
    if not accepted:
        assert "changes/unreleased/new.md" in result.output
        assert _snapshot(root) == before


def test_schedule_uses_latest_released_postponement_by_version_not_filename_or_input_order() -> None:
    def note(directory: str, note_id: str, kind: str, line: str, target: str | None = None) -> Note:
        fields = {"kind": kind, "removes-in": line}
        if target:
            fields["postpones"] = target
        return Note(f"changes/{directory}/{note_id}.md", directory, note_id, fields, "Text.\n")

    notes = [
        note("1.10.0", "a-latest", "changed", "4", "old"),
        note("unreleased", "future", "changed", "5", "old"),
        note("1.9.3", "z-earlier", "changed", "3", "old"),
        note("1.0.0", "old", "deprecated", "2"),
    ]
    assert latest_released_schedule(notes) == {"old": "4"}
    assert release_line("0.12.9") == "0.12"
    assert release_line("2.4.9") == "2"
    assert line_order("0.13") > line_order("0.12")
    assert line_order("2") > line_order("1")


@pytest.mark.parametrize(("new_line", "accepted"), [("3", False), ("2", False), ("4", False), ("5", True)])
def test_postponement_must_advance_latest_released_line_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, new_line: str, accepted: bool
) -> None:
    root = _tree(
        tmp_path,
        {
            "1.0.0/old.md": _scheduled("deprecated", "2"),
            "1.2.0/first.md": _scheduled("changed", "3", target="old"),
            "1.10.0/second.md": _scheduled("changed", "4", target="old"),
            "unreleased/third.md": _scheduled("changed", new_line, target="old"),
        },
    )
    before = _snapshot(root)
    result = _cut(root, "1.11.1", monkeypatch)
    assert (result.exit_code == 0) is accepted, result.output
    if not accepted:
        assert "latest released line 4" in result.output
        assert _snapshot(root) == before


def test_a_postponement_cannot_move_to_the_cut_line_even_if_it_advances_the_old_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(
        tmp_path,
        {
            "0.10.0/old.md": _scheduled("deprecated", "0.11"),
            "unreleased/later.md": _scheduled("changed", "0.12", target="old"),
        },
    )
    before = _snapshot(root)
    result = _cut(root, "0.12.3", monkeypatch)
    assert result.exit_code == 1
    assert "later than cut line 0.12" in result.output
    assert _snapshot(root) == before


def test_pre_one_postponement_must_advance_the_latest_released_minor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(
        tmp_path,
        {
            "0.10.0/old.md": _scheduled("deprecated", "0.11"),
            "0.11.2/first.md": _scheduled("changed", "0.13", target="old"),
            "unreleased/later.md": _scheduled("changed", "0.12", target="old"),
        },
    )
    before = _snapshot(root)
    result = _cut(root, "0.11.3", monkeypatch)
    assert result.exit_code == 1
    assert "latest released line 0.13" in result.output
    assert _snapshot(root) == before


def test_opening_cut_accepts_removal_in_same_candidate_and_refuses_if_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(tmp_path, {"1.9.0/old.md": _scheduled("deprecated", "2"), "unreleased/removal.md": _REMOVAL})
    assert _cut(root, "2.0.0", monkeypatch).exit_code == 0
    (root / "changes" / "2.0.0" / "removal.md").unlink()
    before = _snapshot(root)
    refused = _cut(root, "2.0.0", monkeypatch)
    assert refused.exit_code == 1
    assert "deprecation old is due in 2" in refused.output
    assert _snapshot(root) == before


def test_opening_cut_accepts_postponement_without_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(
        tmp_path,
        {
            "1.9.0/old.md": _scheduled("deprecated", "2"),
            "unreleased/delay.md": _scheduled("changed", "3", target="old"),
        },
    )
    result = _cut(root, "2.0.0", monkeypatch)
    assert result.exit_code == 0, result.output


def test_patch_cut_does_not_enforce_opening_removals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _tree(tmp_path, {"1.9.0/old.md": _scheduled("deprecated", "2"), "unreleased/fix.md": _FIXED})
    result = _cut(root, "2.0.1", monkeypatch)
    assert result.exit_code == 0, result.output
    assert (root / "changes" / "2.0.1" / "fix.md").exists()


def test_opening_cut_checks_all_due_notes_with_mixed_pending_and_released_removals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _tree(
        tmp_path,
        {
            "1.9.0/old.md": _scheduled("deprecated", "2"),
            "1.9.0/other.md": _scheduled("deprecated", "2"),
            "1.9.0/third.md": _scheduled("deprecated", "2"),
            "2.0.0/old-removal.md": _REMOVAL,
            "unreleased/other-removal.md": _REMOVAL.replace("old", "other"),
        },
    )
    before = _snapshot(root)
    refused = _cut(root, "2.0.0", monkeypatch)
    assert refused.exit_code == 1 and "third" in refused.output
    assert "deprecation old is due" not in refused.output
    assert "deprecation other is due" not in refused.output
    assert _snapshot(root) == before
    (root / "changes" / "unreleased" / "third-removal.md").write_text(_REMOVAL.replace("old", "third"))
    assert _cut(root, "2.0.0", monkeypatch).exit_code == 0
