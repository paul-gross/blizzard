"""blizzard:compat — the supported releases, resolved from tag names and read through git on a
real throwaway repository, per ``tests/test_wire_compat.py``."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from blizzard.tools.compat.git import GitError, Repository
from blizzard.tools.compat.releases import (
    Window,
    line_of,
    newest_stable,
    on_line,
    parse_release,
    releases_document,
    supported,
)
from blizzard.wire.version_window import VersionWindow, VersionWindowError

pytestmark = pytest.mark.unit

_TAGS = ["v0.2.0-rc.1", "v0.2.0", "v0.3.0-rc.1"]


def _tags(releases) -> list[str]:
    return [r.tag for r in releases]


def test_the_supported_set_is_the_floor_line_and_the_newest_of_the_major() -> None:
    assert _tags(supported(_TAGS, Window("0.2"))) == ["v0.2.0", "v0.3.0-rc.1"]


def test_a_stable_release_outranks_its_candidates_and_a_later_patch_outranks_both() -> None:
    tags = ["v0.2.1", "v0.2.0", "v0.2.1-rc.2", "v0.2.1-rc.10"]
    assert _tags(supported(tags, Window("0.2"))) == ["v0.2.1"]
    assert _tags(supported(["v0.2.1-rc.2", "v0.2.1-rc.10"], Window("0.2"))) == ["v0.2.1-rc.10"]


def test_the_release_collapses_when_the_floor_line_is_the_newest() -> None:
    assert _tags(supported(["v0.2.0", "v0.2.3"], Window("0.2"))) == ["v0.2.3"]


def test_releases_below_the_floor_are_not_baselines() -> None:
    assert _tags(supported(["v0.1.0-rc.1", "v0.2.0"], Window("0.2"))) == ["v0.2.0"]


def test_a_floor_no_tag_reaches_gives_an_empty_set() -> None:
    assert supported(["v0.1.0-rc.1"], Window("0.2")) == []
    assert supported([], Window("0.2")) == []


def test_each_major_at_or_above_the_floors_contributes_its_newest() -> None:
    tags = ["v0.9.4", "v0.9.5", "v0.11.0", "v1.0.0", "v1.2.0-rc.1", "v1.1.0"]
    assert _tags(supported(tags, Window("0.9"))) == ["v0.9.5", "v0.11.0", "v1.2.0-rc.1"]


def test_non_release_and_malformed_tags_are_ignored() -> None:
    tags = ["nightly", "v1", "v0.2", "v0.2.0.1", "v0.2.0-beta.1", "vx.y.z", "v0.2.0-rc", "v0.2.0"]
    assert _tags(supported(tags, Window("0.2"))) == ["v0.2.0"]


def test_a_line_lists_every_release_cut_on_it_oldest_first() -> None:
    assert _tags(on_line(_TAGS, "0.2")) == ["v0.2.0-rc.1", "v0.2.0"]
    assert on_line(_TAGS, "0.9") == []


def test_a_release_branch_names_its_line() -> None:
    assert line_of("release/0.2") == "0.2"
    with pytest.raises(ValueError, match="release/"):
        line_of("main")


def test_the_newest_stable_release_skips_candidates() -> None:
    assert (newest_stable(_TAGS) or pytest.fail()).tag == "v0.2.0"
    assert newest_stable(["v0.3.0-rc.1"]) is None


def test_the_releases_document_marks_only_the_oldest() -> None:
    document = releases_document(Window("0.2"), supported(_TAGS, Window("0.2")))
    assert document == {
        "floor": "0.2",
        "opening": None,
        "releases": [
            {"tag": "v0.2.0", "line": "0.2", "stable": True, "oldest": True},
            {"tag": "v0.3.0-rc.1", "line": "0.3", "stable": False, "oldest": False},
        ],
    }


def test_parse_release_reads_the_candidate_number() -> None:
    assert (parse_release("v1.2.3-rc.4") or pytest.fail()).candidate == 4
    assert (parse_release("v1.2.3") or pytest.fail()).candidate is None


# --- git ----------------------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    return repo


def _commit(repo: Path, message: str, files: dict[str, str] | None = None) -> None:
    for name, text in (files or {}).items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(text)
    _git(repo, "add", ".")
    _git(repo, "commit", "--allow-empty", "-m", message)


def test_tags_lists_only_v_tags_and_the_set_resolves_from_them(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: base")
    for tag in [*_TAGS, "nightly", "vnext"]:
        _git(repo, "tag", tag)
    tags = Repository(repo).tags()
    assert sorted(tags) == sorted([*_TAGS, "vnext"])
    assert _tags(supported(tags, Window("0.2"))) == ["v0.2.0", "v0.3.0-rc.1"]
    assert _tags(on_line(tags, "0.2")) == ["v0.2.0-rc.1", "v0.2.0"]


def test_show_reads_a_path_at_a_revision_and_reports_a_missing_one_as_absent(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: base", {"a.txt": "one"})
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "feat: more", {"a.txt": "two", "b/c.txt": "new"})
    source = Repository(repo)
    assert source.show("v0.1.0", "a.txt") == "one"
    assert source.show("HEAD", "a.txt") == "two"
    assert source.show("v0.1.0", "b/c.txt") is None


def test_show_still_raises_for_a_revision_that_does_not_exist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: base")
    with pytest.raises(GitError):
        Repository(repo).show("v9.9.9", "a.txt")


def test_subjects_reads_the_commits_of_a_range(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: base")
    _git(repo, "tag", "base")
    _commit(repo, "feat!: drop a route")
    _commit(repo, "fix: tidy")
    assert Repository(repo).subjects("base..HEAD") == ["fix: tidy", "feat!: drop a route"]


# --- the window text parse ------------------------------------------------------------------


def test_the_text_parse_reads_what_load_reads() -> None:
    assert VersionWindow.parse('floor = "0.10"\nopening = "1"\n', "v0.9.0:window.toml") == VersionWindow("0.10", "1")


@pytest.mark.parametrize(
    "text", ["", "floor = 0.2\n", 'floor = "0.2"\nopneing = "1"\n', 'floor = "0.2"\nopening = "x"\n']
)
def test_the_text_parse_rejects_what_load_rejects_naming_its_source(text: str) -> None:
    with pytest.raises(VersionWindowError, match=re.escape("v0.9.0:window.toml")):
        VersionWindow.parse(text, "v0.9.0:window.toml")


def test_names_lists_the_files_under_a_directory_at_a_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: base", {"changes/0.1.0/b.md": "b", "changes/0.1.0/a.md": "a", "changes/README.md": "r"})
    assert Repository(repo).names("HEAD", "changes/0.1.0") == ["a.md", "b.md"]
    assert Repository(repo).names("HEAD", "changes") == ["0.1.0", "README.md"]
    assert Repository(repo).names("HEAD", "absent") == []


def test_previous_tag_is_the_nearest_tag_before_a_revision_or_none(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _commit(repo, "chore: one")
    _git(repo, "tag", "v0.1.0")
    _commit(repo, "chore: two")
    _git(repo, "tag", "v0.2.0")
    assert Repository(repo).previous_tag("v0.2.0") == "v0.1.0"
    assert Repository(repo).previous_tag("v0.1.0") is None
