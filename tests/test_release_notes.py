"""``scripts/release-notes.sh`` — the Conventional-Commit-type grouping,
pinned over a synthetic commit list so this needs no real git history.
The script's ``--range`` mode (real ``git log``) is exercised only by the
release workflow itself; ``--from-stdin`` is what this test drives.
"""

from __future__ import annotations

import subprocess

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_SCRIPT = repo_root() / "scripts" / "release-notes.sh"


def _run(commit_lines: list[str]) -> str:
    result = subprocess.run(
        [str(_SCRIPT), "--from-stdin"],
        input="\n".join(commit_lines) + "\n",
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_commits_are_grouped_by_conventional_type() -> None:
    output = _run(
        [
            "abc123 feat(hub): add thing",
            "def456 fix(runner): fix bug",
            "ghi789 docs: update readme",
            "jkl012 chore: bump deps",
        ]
    )
    assert "## Features" in output
    assert "- add thing (abc123)" in output
    assert "## Fixes" in output
    assert "- fix bug (def456)" in output
    assert "## Documentation" in output
    assert "- update readme (ghi789)" in output
    assert "## Chores" in output
    assert "- bump deps (jkl012)" in output


def test_section_order_is_stable() -> None:
    output = _run(
        [
            "a chore: z",
            "b docs: y",
            "c fix: x",
            "d feat: w",
        ]
    )
    assert output.index("## Features") < output.index("## Fixes")
    assert output.index("## Fixes") < output.index("## Documentation")
    assert output.index("## Documentation") < output.index("## Chores")


def test_breaking_marker_subjects_stay_listed_under_their_type() -> None:
    output = _run(["aaa feat: a normal feature", "bbb feat(hub)!: a breaking one", "ccc fix!: also breaking"])
    assert "## Breaking changes" not in output
    features = output[output.index("## Features") : output.index("## Fixes")]
    assert "- a breaking one (bbb)" in features
    assert "- also breaking (ccc)" in output


def test_upgrade_notes_heading_is_not_emitted() -> None:
    assert "## Upgrade notes" not in _run(["aaa feat: a normal feature"])


def test_non_conventional_subject_lands_in_other() -> None:
    output = _run(["aaa a subject with no conventional-commit prefix"])
    assert "## Other" in output
    assert "- a subject with no conventional-commit prefix (aaa)" in output


def test_empty_input_emits_no_headings() -> None:
    assert _run([]).strip() == ""
