"""blizzard:unit-test for the change-note validator — fixture ``changes/`` trees built under ``tmp_path``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from blizzard.tools.change_notes.anchors import anchors
from blizzard.tools.change_notes.cli import main
from blizzard.tools.change_notes.contract import Failure, Note, check_note
from blizzard.tools.change_notes.tree import parse_note
from blizzard.tools.change_notes.validate import validate
from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_VALID = "---\nkind: added\n---\n\nAdds a thing.\n"


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    (tmp_path / "changes" / "unreleased").mkdir(parents=True)
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return tmp_path


def _note(frontmatter: str, body: str = "Adds a thing.\n") -> str:
    return f"---\n{frontmatter}\n---\n\n{body}"


def _pairs(failures: list[Failure]) -> set[tuple[str, str]]:
    return {(f.path, f.rule) for f in failures}


def _only(tmp_path: Path, rel: str, text: str, **extra: str) -> set[tuple[str, str]]:
    return _pairs(validate(_tree(tmp_path, {rel: text, **extra})))


_UNR = "changes/unreleased/thing.md"
_DEPRECATION = "changes/1.0.0/old-thing.md"
_DEPRECATION_TEXT = _note('kind: deprecated\nremoves-in: "2"')


def test_an_empty_unreleased_directory_passes(tmp_path: Path) -> None:
    root = _tree(tmp_path, {"changes/unreleased/.gitkeep": "", "changes/README.md": "# Notes\n"})
    assert validate(root) == []


def test_a_valid_note_passes(tmp_path: Path) -> None:
    assert _only(tmp_path, _UNR, _VALID) == set()


def test_a_file_outside_the_directory_shapes_fails(tmp_path: Path) -> None:
    assert ("changes/stray.md", "bad-path") in _only(tmp_path, "changes/stray.md", _VALID)


def test_a_directory_that_is_not_a_version_fails(tmp_path: Path) -> None:
    assert ("changes/v1.0.0", "bad-path") in _pairs(validate(_tree(tmp_path, {"changes/v1.0.0/a.md": _VALID})))


def test_a_version_with_a_leading_zero_fails(tmp_path: Path) -> None:
    assert ("changes/01.0.0", "bad-path") in _pairs(validate(_tree(tmp_path, {"changes/01.0.0/a.md": _VALID})))


def test_a_non_markdown_file_in_a_note_directory_fails(tmp_path: Path) -> None:
    assert ("changes/unreleased/a.txt", "bad-path") in _only(tmp_path, "changes/unreleased/a.txt", _VALID)


def test_a_gitkeep_outside_unreleased_fails(tmp_path: Path) -> None:
    assert ("changes/1.0.0/.gitkeep", "bad-path") in _only(tmp_path, "changes/1.0.0/.gitkeep", "")


def test_an_id_with_a_bad_shape_fails(tmp_path: Path) -> None:
    assert ("changes/unreleased/Bad_Id.md", "bad-id") in _only(tmp_path, "changes/unreleased/Bad_Id.md", _VALID)


def test_an_id_over_sixty_characters_fails(tmp_path: Path) -> None:
    rel = f"changes/unreleased/{'a' * 61}.md"
    assert (rel, "id-too-long") in _only(tmp_path, rel, _VALID)


def test_an_id_of_sixty_characters_passes(tmp_path: Path) -> None:
    assert _only(tmp_path, f"changes/unreleased/{'a' * 60}.md", _VALID) == set()


def test_a_repeated_id_across_directories_fails_on_both_files(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _VALID, **{"changes/1.0.0/thing.md": _VALID})
    assert found == {(_UNR, "duplicate-id"), ("changes/1.0.0/thing.md", "duplicate-id")}


def test_a_file_without_frontmatter_fails(tmp_path: Path) -> None:
    assert (_UNR, "no-frontmatter") in _only(tmp_path, _UNR, "Just prose.\n")


def test_frontmatter_that_is_not_yaml_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-frontmatter") in _only(tmp_path, _UNR, _note("kind: [unclosed"))


def test_frontmatter_that_is_not_a_mapping_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-frontmatter") in _only(tmp_path, _UNR, _note("- added"))


def test_a_repeated_frontmatter_key_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-frontmatter") in _only(tmp_path, _UNR, _note("kind: added\nkind: fixed"))


def test_an_unknown_field_fails(tmp_path: Path) -> None:
    assert (_UNR, "unknown-field") in _only(tmp_path, _UNR, _note("kind: added\nmood: sunny"))


def test_a_missing_kind_fails(tmp_path: Path) -> None:
    assert (_UNR, "missing-kind") in _only(tmp_path, _UNR, _note("surfaces: [cli]"))


def test_an_unknown_kind_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-kind") in _only(tmp_path, _UNR, _note("kind: improved"))


def test_a_list_valued_kind_fails_instead_of_crashing(tmp_path: Path) -> None:
    assert (_UNR, "bad-kind") in _only(tmp_path, _UNR, _note("kind: [added]"))


def test_a_mapping_valued_kind_with_breaking_fails_instead_of_crashing(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: {a: b}\nbreaking: true"))
    assert {(_UNR, "bad-kind"), (_UNR, "breaking-kind")} <= found


def test_an_unknown_surface_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-surfaces") in _only(tmp_path, _UNR, _note("kind: added\nsurfaces: [moon]"))


def test_a_repeated_surface_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-surfaces") in _only(tmp_path, _UNR, _note("kind: added\nsurfaces: [cli, cli]"))


def test_breaking_on_an_added_note_fails(tmp_path: Path) -> None:
    text = _note("kind: added\nbreaking: true", "Adds.\n\n## Upgrade\n\nDo this.\n")
    assert _only(tmp_path, _UNR, text) == {(_UNR, "breaking-kind")}


def test_breaking_on_a_changed_note_passes(tmp_path: Path) -> None:
    text = _note("kind: changed\nbreaking: true", "Changes.\n\n## Upgrade\n\nDo this.\n")
    assert _only(tmp_path, _UNR, text) == set()


def test_breaking_that_is_not_a_boolean_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-breaking") in _only(tmp_path, _UNR, _note("kind: changed\nbreaking: yes please"))


def test_breaks_without_breaking_fails(tmp_path: Path) -> None:
    assert (_UNR, "breaks-not-breaking") in _only(tmp_path, _UNR, _note("kind: changed\nbreaks: [a thing]"))


def test_breaks_that_is_not_a_list_of_strings_fails(tmp_path: Path) -> None:
    text = _note("kind: changed\nbreaking: true\nbreaks: a thing", "Changes.\n\n## Upgrade\n\nDo this.\n")
    assert (_UNR, "bad-breaks") in _only(tmp_path, _UNR, text)


def test_removes_in_must_be_quoted(tmp_path: Path) -> None:
    assert (_UNR, "bad-removes-in") in _only(tmp_path, _UNR, _note("kind: deprecated\nremoves-in: 2"))


def test_removes_in_rejects_a_zero_major(tmp_path: Path) -> None:
    assert (_UNR, "bad-removes-in") in _only(tmp_path, _UNR, _note('kind: deprecated\nremoves-in: "0"'))


def test_removes_in_accepts_a_pre_one_minor(tmp_path: Path) -> None:
    assert _only(tmp_path, _UNR, _note('kind: deprecated\nremoves-in: "0.12"')) == set()


def test_a_deprecation_without_removes_in_fails(tmp_path: Path) -> None:
    assert (_UNR, "removes-in-required") in _only(tmp_path, _UNR, _note("kind: deprecated"))


def test_removes_in_on_an_added_note_fails(tmp_path: Path) -> None:
    assert (_UNR, "removes-in-misplaced") in _only(tmp_path, _UNR, _note('kind: added\nremoves-in: "2"'))


def test_postpones_on_a_non_changed_note_fails(tmp_path: Path) -> None:
    found = _only(
        tmp_path, _UNR, _note('kind: fixed\npostpones: old-thing\nremoves-in: "3"'), **{_DEPRECATION: _DEPRECATION_TEXT}
    )
    assert found == {(_UNR, "postpones-kind")}


def test_postpones_without_removes_in_fails(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: changed\npostpones: old-thing"), **{_DEPRECATION: _DEPRECATION_TEXT})
    assert found == {(_UNR, "removes-in-required")}


def test_postpones_a_released_deprecation_passes(tmp_path: Path) -> None:
    text = _note('kind: changed\npostpones: old-thing\nremoves-in: "3"')
    assert _only(tmp_path, _UNR, text, **{_DEPRECATION: _DEPRECATION_TEXT}) == set()


def test_retires_on_a_non_removed_note_fails(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: fixed\nretires: old-thing"), **{_DEPRECATION: _DEPRECATION_TEXT})
    assert found == {(_UNR, "retires-kind")}


def test_retires_a_released_deprecation_passes(tmp_path: Path) -> None:
    text = _note("kind: removed\nretires: old-thing", "Removes.\n\n## Upgrade\n\nDo this.\n")
    assert _only(tmp_path, _UNR, text, **{_DEPRECATION: _DEPRECATION_TEXT}) == set()


def test_retires_naming_no_note_fails(tmp_path: Path) -> None:
    text = _note("kind: removed\nretires: ghost", "Removes.\n\n## Upgrade\n\nDo this.\n")
    assert _only(tmp_path, _UNR, text) == {(_UNR, "unresolved-retires")}


def test_retires_naming_an_unreleased_deprecation_fails(tmp_path: Path) -> None:
    text = _note("kind: removed\nretires: old-thing", "Removes.\n\n## Upgrade\n\nDo this.\n")
    found = _only(tmp_path, _UNR, text, **{"changes/unreleased/old-thing.md": _DEPRECATION_TEXT})
    assert found == {(_UNR, "unresolved-retires")}


def test_retires_naming_a_released_non_deprecation_fails(tmp_path: Path) -> None:
    text = _note("kind: removed\nretires: old-thing", "Removes.\n\n## Upgrade\n\nDo this.\n")
    assert _only(tmp_path, _UNR, text, **{_DEPRECATION: _VALID}) == {(_UNR, "unresolved-retires")}


def test_postpones_naming_no_note_fails(tmp_path: Path) -> None:
    text = _note('kind: changed\npostpones: ghost\nremoves-in: "3"')
    assert _only(tmp_path, _UNR, text) == {(_UNR, "unresolved-postpones")}


def test_flag_is_refused_until_the_lifecycle_check_exists(tmp_path: Path) -> None:
    failures = validate(_tree(tmp_path, {_UNR: _note("kind: added\nflag: some-flag")}))
    assert _pairs(failures) == {(_UNR, "flag-unsupported")}
    assert "flag lifecycle check" in failures[0].detail


def test_an_upgrade_guide_anchor_that_exists_passes(tmp_path: Path) -> None:
    docs = {"docs/upgrade.md": "# Upgrade\n\n### Host graduation boundary\n"}
    assert _only(tmp_path, _UNR, _note("kind: added\nupgrade-guide: host-graduation-boundary"), **docs) == set()


def test_an_upgrade_guide_anchor_the_doc_lacks_fails(tmp_path: Path) -> None:
    docs = {"docs/upgrade.md": "# Upgrade\n"}
    assert _only(tmp_path, _UNR, _note("kind: added\nupgrade-guide: nowhere"), **docs) == {(_UNR, "unknown-anchor")}


def test_an_upgrade_guide_without_the_doc_fails(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: added\nupgrade-guide: anything"))
    assert found == {(_UNR, "missing-upgrade-doc")}


def test_an_upgrade_guide_with_a_leading_hash_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-upgrade-guide") in _only(tmp_path, _UNR, _note("kind: added\nupgrade-guide: '#anchor'"))


def test_an_empty_body_fails(tmp_path: Path) -> None:
    assert (_UNR, "empty-body") in _only(tmp_path, _UNR, "---\nkind: added\n---\n\n  \n")


def test_a_heading_other_than_upgrade_fails(tmp_path: Path) -> None:
    assert (_UNR, "bad-heading") in _only(tmp_path, _UNR, _note("kind: added", "Adds.\n\n## Notes\n\nMore.\n"))


def test_a_heading_inside_fenced_code_is_not_a_heading(tmp_path: Path) -> None:
    assert _only(tmp_path, _UNR, _note("kind: added", "Adds.\n\n```\n## Notes\n```\n")) == set()


def test_a_repeated_upgrade_heading_fails(tmp_path: Path) -> None:
    body = "Adds.\n\n## Upgrade\n\nOne.\n\n## Upgrade\n\nTwo.\n"
    assert (_UNR, "duplicate-upgrade") in _only(tmp_path, _UNR, _note("kind: added", body))


def test_an_empty_upgrade_section_fails(tmp_path: Path) -> None:
    assert (_UNR, "empty-upgrade") in _only(tmp_path, _UNR, _note("kind: added", "Adds.\n\n## Upgrade\n"))


def test_a_body_that_opens_with_a_heading_fails(tmp_path: Path) -> None:
    assert (_UNR, "empty-body") in _only(tmp_path, _UNR, _note("kind: added", "## Upgrade\n\nDo this.\n"))


def test_a_breaking_note_without_upgrade_fails(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: changed\nbreaking: true"))
    assert found == {(_UNR, "upgrade-required")}


def test_a_removed_note_without_upgrade_fails(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: removed"))
    assert found == {(_UNR, "upgrade-required")}


def test_every_failure_is_reported_not_the_first(tmp_path: Path) -> None:
    found = _only(tmp_path, _UNR, _note("kind: improved\nmood: sunny"))
    assert {rule for _, rule in found} == {"bad-kind", "unknown-field"}


def test_anchors_follow_github_slugs_and_number_repeats() -> None:
    text = "# A `Code` Title!\n\n## Same\n\n## Same\n\n```\n# Hidden\n```\n"
    assert anchors(text) == {"a-code-title", "same", "same-1"}


def test_check_exits_zero_on_a_valid_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_tree(tmp_path, {_UNR: _VALID}))
    result = CliRunner().invoke(main, ["check"])
    assert result.exit_code == 0
    assert "change notes ok" in result.output


def test_check_exits_one_naming_the_file_and_rule(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(_tree(tmp_path, {_UNR: _note("kind: improved")}))
    result = CliRunner().invoke(main, ["check"])
    assert result.exit_code == 1
    assert f"{_UNR}: bad-kind:" in result.output


def test_check_without_a_changes_directory_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    assert CliRunner().invoke(main, ["check"]).exit_code == 2


def test_the_readme_has_one_valid_example_per_kind() -> None:
    readme = (repo_root() / "changes" / "README.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```markdown\n(.*?)```", readme, re.DOTALL)
    kinds = []
    for block in blocks:
        parsed = parse_note(block, path="README.md", directory="unreleased", note_id="example")
        assert isinstance(parsed, Note)
        kinds.append(parsed.fields["kind"])
        assert check_note(parsed) == []
    assert sorted(kinds) == sorted(["added", "changed", "deprecated", "removed", "fixed", "security"])
