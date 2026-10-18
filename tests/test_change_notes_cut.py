"""blizzard:unit-test for ``blizzard-change-notes cut`` — fixture trees built under ``tmp_path``."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.cli import main
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
