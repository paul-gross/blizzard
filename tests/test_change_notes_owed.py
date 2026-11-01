"""blizzard:change-notes — ``owed``: what a range owes, on real throwaway repositories."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.tools.change_notes.cli import main

pytestmark = pytest.mark.unit

_HUB = "openapi/hub.openapi.json"
_RUNNER_CLI = "contracts/cli/runner.json"
_STORE = "contracts/store/hub.json"
_UNRELEASED = "changes/unreleased/new-thing.md"
_RELEASED = "changes/0.2.0/old-thing.md"
_WAIVER = "Change-Note: none — reorders an enum, nothing an operator sees"


def _note(*, breaking: bool = False, upgrade: bool = False, body: str = "Something changed.") -> str:
    head = "---\nkind: changed\nsurfaces: [cli]\n" + ("breaking: true\nbreaks: [x]\n" if breaking else "") + "---\n\n"
    return head + body + ("\n\n## Upgrade\n\nDo this.\n" if upgrade else "\n")


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")
        self.write({_HUB: json.dumps({"v": 1}), _RUNNER_CLI: "{}", _STORE: "{}", _RELEASED: _note()})
        self.commit("chore: seed")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def write(self, files: dict[str, str]) -> None:
        for path, text in files.items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)

    def commit(self, message: str, files: dict[str, str] | None = None, *, trailer: str | None = None) -> None:
        self.write(files or {})
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-m", message + (f"\n\n{trailer}" if trailer else ""))


@pytest.fixture
def repo(tmp_path: Path) -> _Repo:
    repo = _Repo(tmp_path / "repo")
    repo.git("branch", "base")
    repo.git("switch", "-c", "feature")
    return repo


def _owed(repo: _Repo, monkeypatch: pytest.MonkeyPatch, against: str = "base") -> Result:
    monkeypatch.chdir(repo.root)
    return CliRunner().invoke(main, ["owed", "--against", against])


def _edit_hub(repo: _Repo, message: str = "feat: change the hub", **kwargs) -> None:
    repo.commit(message, {_HUB: json.dumps({"v": 2}), **kwargs.pop("files", {})}, **kwargs)


def test_a_changed_openapi_document_with_no_note_fails_naming_the_document(
    repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    _edit_hub(repo)
    result = _owed(repo, monkeypatch)
    assert result.exit_code == 1
    assert _HUB in result.output


def test_an_added_unreleased_note_satisfies_the_trigger(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _edit_hub(repo, files={_UNRELEASED: _note()})
    assert _owed(repo, monkeypatch).exit_code == 0


def test_a_reasoned_waiver_passes_and_prints_its_reason(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _edit_hub(repo, trailer=_WAIVER)
    result = _owed(repo, monkeypatch)
    assert result.exit_code == 0
    assert "reorders an enum" in result.output


def test_a_waiver_without_a_reason_fails(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    _edit_hub(repo, trailer="Change-Note: none")
    result = _owed(repo, monkeypatch)
    assert result.exit_code == 1
    assert "Change-Note: none — <reason>" in result.output


def test_a_changed_cli_contract_owes_a_note(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat: a verb", {_RUNNER_CLI: '{"commands": []}'})
    assert _owed(repo, monkeypatch).exit_code == 1


def test_a_changed_store_document_alone_owes_nothing(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("fix: store", {_STORE: '{"tables": {}}'})
    assert _owed(repo, monkeypatch).exit_code == 0


def test_a_release_cut_range_is_exempt_from_the_trigger_and_may_move_notes_into_its_version(
    repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.commit("feat: pending", {"changes/unreleased/pending.md": _note()})
    repo.git("tag", "mark")
    (repo.root / "changes/0.3.0").mkdir()
    repo.git("mv", "changes/unreleased/pending.md", "changes/0.3.0/pending.md")
    repo.commit("chore(release): cut 0.3.0", {_HUB: json.dumps({"v": 3})}, trailer="Release-Cut: 0.3.0")
    result = _owed(repo, monkeypatch, "mark")
    assert result.exit_code == 0, result.output
    assert "cut of 0.3.0" in result.output


def test_a_cut_may_not_move_notes_into_another_version(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat: pending", {"changes/unreleased/pending.md": _note()})
    repo.git("tag", "mark")
    (repo.root / "changes/0.9.0").mkdir()
    repo.git("mv", "changes/unreleased/pending.md", "changes/0.9.0/pending.md")
    repo.commit("chore(release): cut 0.3.0", trailer="Release-Cut: 0.3.0")
    result = _owed(repo, monkeypatch, "mark")
    assert result.exit_code == 1
    assert "changes/0.9.0/pending.md" in result.output


def test_a_bang_commit_without_a_breaking_note_fails(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat!: drop it")
    assert _owed(repo, monkeypatch).exit_code == 1


def test_a_bang_commit_whose_breaking_note_lacks_upgrade_fails(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat!: drop it", {_UNRELEASED: _note(breaking=True)})
    result = _owed(repo, monkeypatch)
    assert result.exit_code == 1
    assert "Upgrade" in result.output


def test_a_bang_commit_with_a_breaking_upgrade_note_passes(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat!: drop it", {_UNRELEASED: _note(breaking=True, upgrade=True)})
    assert _owed(repo, monkeypatch).exit_code == 0


def test_a_breaking_note_without_a_bang_commit_fails_and_no_waiver_helps(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("feat: drop it", {_UNRELEASED: _note(breaking=True, upgrade=True)}, trailer=_WAIVER)
    assert _owed(repo, monkeypatch).exit_code == 1


def test_flipping_an_existing_unreleased_note_to_breaking_counts_as_adding_one(
    repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo.git("switch", "base")
    repo.commit("feat: pending", {_UNRELEASED: _note()})
    repo.git("switch", "feature")
    repo.git("reset", "--hard", "base")
    repo.commit("feat: flip", {_UNRELEASED: _note(breaking=True, upgrade=True)})
    assert _owed(repo, monkeypatch).exit_code == 1


def test_a_released_notes_frontmatter_is_held_and_its_body_is_free(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.commit("docs: reword", {_RELEASED: _note(body="Reworded.")})
    assert _owed(repo, monkeypatch).exit_code == 0
    repo.commit("docs: retag", {_RELEASED: _note().replace("changed", "fixed")})
    result = _owed(repo, monkeypatch)
    assert result.exit_code == 1
    assert _RELEASED in result.output


@pytest.mark.parametrize("action", ["add", "delete", "rename"])
def test_adding_deleting_or_renaming_a_released_note_fails(repo, monkeypatch: pytest.MonkeyPatch, action) -> None:
    if action == "add":
        repo.commit("docs: add", {"changes/0.2.0/extra.md": _note()})
    elif action == "delete":
        repo.git("rm", "-q", _RELEASED)
        repo.commit("docs: delete")
    else:
        repo.git("mv", _RELEASED, "changes/0.2.0/renamed.md")
        repo.commit("docs: rename")
    assert _owed(repo, monkeypatch).exit_code == 1


def test_commits_reachable_from_the_ref_stay_outside_the_range(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    repo.git("switch", "main")
    repo.commit("feat!: breaking on the base", {_HUB: json.dumps({"v": 9})})
    repo.git("branch", "-f", "base")
    repo.git("switch", "feature")
    repo.commit("docs: unrelated", {"README.md": "x"})
    repo.git("merge", "--no-edit", "base")
    assert _owed(repo, monkeypatch).exit_code == 0


def test_an_unknown_ref_is_a_usage_error_not_a_pass(repo, monkeypatch: pytest.MonkeyPatch) -> None:
    result = _owed(repo, monkeypatch, "no-such-ref")
    assert result.exit_code == 2
