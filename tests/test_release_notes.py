"""``scripts/release-notes.sh`` — the Conventional-Commit-type grouping (issue
#190), pinned over a synthetic commit list so this needs no real git history.
The script's ``--range`` mode (real ``git log``) is exercised only by the
release workflow itself; ``--from-stdin`` is what this test drives.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "release-notes.sh"


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


def test_breaking_change_marker_surfaces_at_the_top() -> None:
    output = _run(
        [
            "aaa feat: a normal feature",
            "bbb feat(hub)!: a breaking one",
            "ccc fix!: also breaking",
        ]
    )
    breaking_idx = output.index("## Breaking changes")
    features_idx = output.index("## Features")
    assert breaking_idx < features_idx
    breaking_section = output[breaking_idx:features_idx]
    assert "- a breaking one (bbb)" in breaking_section
    assert "- also breaking (ccc)" in breaking_section
    assert "- a normal feature" not in breaking_section


def test_no_breaking_changes_section_omitted_when_none_present() -> None:
    output = _run(["aaa feat: a normal feature"])
    assert "## Breaking changes" not in output


def test_upgrade_notes_placeholder_always_present() -> None:
    output = _run(["aaa feat: a normal feature"])
    assert "## Upgrade notes" in output


def test_non_conventional_subject_lands_in_other() -> None:
    output = _run(["aaa a subject with no conventional-commit prefix"])
    assert "## Other" in output
    assert "- a subject with no conventional-commit prefix (aaa)" in output


def test_empty_input_still_emits_the_upgrade_notes_placeholder() -> None:
    output = _run([])
    assert "## Upgrade notes" in output
    assert "## Breaking changes" not in output
    assert "## Other" not in output
