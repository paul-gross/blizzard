"""blizzard:compat — the gate decision and the CLI, on real throwaway repositories whose tags hold
hand-built contract documents."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from blizzard.tools.compat import gate
from blizzard.tools.compat.cli import main
from blizzard.tools.compat.releases import Window
from blizzard.tools.compat.violation import Violation

pytestmark = pytest.mark.unit

_ROUTE = "/api/fleet/widgets"
_EMPTY = {
    "openapi/hub.openapi.json": {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {}}},
    "openapi/runner.openapi.json": {"openapi": "3.1.0", "paths": {}, "components": {"schemas": {}}},
    "contracts/config/hub.json": {"daemon": "hub", "keys": [], "paths": []},
    "contracts/config/host.json": {"daemon": "host", "keys": [], "paths": []},
    "contracts/traces/dictionary.json": {},
    "contracts/egress/dictionary.json": {"datasets": []},
    "contracts/store/hub.json": {"tables": {}},
    "contracts/store/runner.json": {"tables": {}},
}
_WINDOW = "src/blizzard/wire/window.toml"


def _spec(*, route: bool, deprecated: bool = False) -> dict:
    document = json.loads(json.dumps(_EMPTY["openapi/hub.openapi.json"]))
    if route:
        operation: dict = {"responses": {"204": {"description": "ok"}}}
        if deprecated:
            operation["deprecated"] = True
        document["paths"][_ROUTE] = {"get": operation}
    return document


class _Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir()
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.com")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(
        self,
        message: str,
        *,
        floor: str = "0.2",
        opening: str | None = None,
        hub: dict | None = None,
        drop: tuple[str, ...] = (),
        notes: dict[str, str] | None = None,
        store: dict | None = None,
        **extra,
    ) -> str:
        documents = {**_EMPTY, "openapi/hub.openapi.json": hub or _EMPTY["openapi/hub.openapi.json"], **extra}
        if store is not None:
            documents["contracts/store/hub.json"] = store
        for path, document in documents.items():
            target = self.root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(document))
        for path in drop:
            (self.root / path).unlink(missing_ok=True)
        window = self.root / _WINDOW
        window.parent.mkdir(parents=True, exist_ok=True)
        window.write_text(f'floor = "{floor}"\n' + (f'opening = "{opening}"\n' if opening else ""))
        for path, text in (notes or {}).items():
            (self.root / path).parent.mkdir(parents=True, exist_ok=True)
            (self.root / path).write_text(text)
        self.git("add", "-A")
        self.git("commit", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD")


def _note(*, surfaces: str = "wire", breaks: str = _ROUTE) -> str:
    return (
        f"---\nkind: changed\nsurfaces: [{surfaces}]\nbreaking: true\nbreaks: [{breaks}]\n---\n\n"
        "The widgets route is gone.\n\n## Upgrade\n\nStop calling it.\n"
    )


_NOTE_PATH = "changes/unreleased/drop-widgets.md"


def _run(repo: _Repo, monkeypatch: pytest.MonkeyPatch, *args: str) -> Result:
    monkeypatch.chdir(repo.root)
    return CliRunner().invoke(main, list(args))


def _shipped(repo: _Repo, *, deprecated_in: dict[str, bool]) -> str:
    """Tag each release with a hub document carrying the route, deprecated or not; return a base commit."""
    for tag, deprecated in deprecated_in.items():
        repo.commit(f"chore: cut {tag}", hub=_spec(route=True, deprecated=deprecated))
        repo.git("tag", tag)
    return repo.git("rev-parse", "HEAD")


# --- the pure decision ----------------------------------------------------------------------


def _finding(*, deprecated: bool) -> Violation:
    return Violation("wire", _ROUTE, "surface path removed", deprecated)


def test_a_deprecated_finding_passes_only_when_the_change_acknowledges_a_break() -> None:
    findings = {"v0.2.0": [_finding(deprecated=True)]}
    assert [v.passes for v in gate.judge(findings, acknowledged=True, line=False)] == [True]
    assert [v.passes for v in gate.judge(findings, acknowledged=False, line=False)] == [False]


def test_an_undeprecated_finding_never_passes() -> None:
    assert [v.passes for v in gate.judge({"v0.2.0": [_finding(deprecated=False)]}, acknowledged=True, line=False)] == [
        False
    ]


def test_on_a_release_line_nothing_passes() -> None:
    assert [v.passes for v in gate.judge({"v0.2.0": [_finding(deprecated=True)]}, acknowledged=True, line=True)] == [
        False
    ]


def test_the_acknowledging_marker_reads_a_subject_with_or_without_a_scope() -> None:
    assert gate.is_acknowledged(["fix: a", "feat(runner)!: drop"])
    assert not gate.is_acknowledged(["feat(runner): add", "fix: wording ! not a marker"])


def test_an_opening_at_or_below_the_newest_stable_major_is_a_finding_naming_both() -> None:
    findings = gate.judge_window(Window("0.2", "1"), None, base_verdicts=[], head_verdicts=[], newest_stable_major=1)
    assert len(findings) == 1
    assert "opening 1" in findings[0].reason and "major 1" in findings[0].reason
    assert not gate.judge_window(Window("0.2", "2"), None, base_verdicts=[], head_verdicts=[], newest_stable_major=1)


# --- the CLI on throwaway repositories -----------------------------------------------------------


def test_a_removed_deprecated_route_passes_with_a_marked_commit_in_the_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": True})
    repo.commit("feat!: remove the widgets route")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 0, result.output
    assert "allowed" in result.output


def test_a_removed_deprecated_route_fails_without_a_marked_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": True})
    repo.commit("feat: remove the widgets route")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "BREAK" in result.output


def test_without_a_range_no_finding_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": True})
    repo.commit("feat!: remove the widgets route")
    assert _run(repo, monkeypatch).exit_code == 1


def test_a_removal_fails_when_one_supported_release_does_not_mark_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False, "v0.3.0-rc.1": True})
    repo.commit("feat!: remove the widgets route")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "supported releases: v0.2.0, v0.3.0-rc.1" in result.output


def test_a_line_holds_every_release_on_it_and_lets_nothing_through(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0-rc.1": True, "v0.2.0": True, "v0.3.0": True})
    repo.commit("feat!: remove the widgets route")
    result = _run(repo, monkeypatch, "--line", "release/0.2", "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "supported releases: v0.2.0-rc.1, v0.2.0" in result.output


def test_an_additive_change_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.commit("feat: deprecate the route", hub=_spec(route=True, deprecated=True))
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 0, result.output
    assert "additive" in result.output


def test_a_floor_raised_to_a_line_that_deprecates_the_removal_passes_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False, "v0.3.0": True})
    repo.commit("feat!: remove the widgets route", floor="0.3")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 0, result.output


def test_the_same_removal_without_raising_the_floor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False, "v0.3.0": True})
    repo.commit("feat!: remove the widgets route")
    assert _run(repo, monkeypatch, "--commits", f"{base}..HEAD").exit_code == 1


def test_a_floor_raised_with_no_finding_needing_it_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": True, "v0.3.0": True})
    repo.commit("feat: raise the floor", hub=_spec(route=True, deprecated=True), floor="0.3")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "floor raised from 0.2 to 0.3" in result.output


def test_a_lowered_floor_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base", floor="0.3")
    base = repo.git("rev-parse", "HEAD")
    repo.commit("feat: lower the floor", floor="0.2")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "floor lowered from 0.3 to 0.2" in result.output


def test_an_opening_at_or_below_the_newest_stable_major_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base")
    repo.git("tag", "v1.0.0")
    (repo.root / _WINDOW).write_text('floor = "0.2"\nopening = "1"\n')
    repo.git("commit", "-am", "feat: reopen a shipped major")
    result = _run(repo, monkeypatch, "--commits", "HEAD~1..HEAD")
    assert result.exit_code == 1
    assert "opening 1" in result.output


def test_a_release_missing_a_document_has_no_baseline_for_it_and_the_rest_is_still_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: cut", hub=_spec(route=True), drop=("contracts/config/hub.json",))
    repo.git("tag", "v0.2.0")
    repo.commit("feat: remove the route")
    result = _run(repo, monkeypatch)
    assert result.exit_code == 1
    assert "contracts/config/hub.json (config): no baseline" in result.output
    assert "BREAK" in result.output


def test_head_missing_a_document_is_an_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: cut", drop=("contracts/store/hub.json",))
    result = _run(repo, monkeypatch)
    assert result.exit_code != 0
    assert "HEAD has no contracts/store/hub.json" in result.output


def test_releases_json_emits_the_baseline_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False, "v0.2.0": False})
    repo.git("tag", "v0.3.0-rc.1")
    result = _run(repo, monkeypatch, "releases", "--json")
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "floor": "0.2",
        "opening": None,
        "releases": [
            {"tag": "v0.2.0", "line": "0.2", "stable": True, "oldest": True},
            {"tag": "v0.3.0-rc.1", "line": "0.3", "stable": False, "oldest": False},
        ],
    }


def test_releases_line_lists_the_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False, "v0.2.0": False})
    result = _run(repo, monkeypatch, "releases", "--line", "release/0.2")
    assert result.output.split() == ["v0.2.0-rc.1", "v0.2.0"]


def test_an_empty_supported_set_exits_zero_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base")
    repo.git("tag", "v0.1.0-rc.1")
    listed = _run(repo, monkeypatch, "releases", "--json")
    assert listed.exit_code == 0 and json.loads(listed.stdout)["releases"] == []
    checked = _run(repo, monkeypatch)
    assert checked.exit_code == 0
    assert "no release is supported yet" in checked.output


def test_a_malformed_range_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base")
    assert _run(repo, monkeypatch, "--commits", "HEAD").exit_code != 0


@pytest.mark.parametrize("args", [("releases",), ("releases", "--json"), ()])
def test_outside_a_git_repository_the_command_ends_with_a_one_line_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    result = CliRunner().invoke(main, list(args))
    assert result.exit_code == 1
    assert result.output.startswith("Error: git rev-parse --show-toplevel failed")
    assert "Traceback" not in result.output


# --- inherited findings ---------------------------------------------------------------------------


def test_a_finding_the_base_already_carries_is_inherited_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.commit("feat: remove the widgets route")
    base = repo.git("rev-parse", "HEAD")
    repo.commit("feat: unrelated")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 0, result.output
    assert "[inherited]" in result.output


def test_a_new_finding_beside_an_inherited_one_still_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    table = {"tables": {"t": {"columns": {}}}}
    repo.commit("chore: cut", hub=_spec(route=True), store=table)
    repo.git("tag", "v0.2.0")
    repo.commit("feat: remove the widgets route", store=table)
    base = repo.git("rev-parse", "HEAD")
    repo.commit("feat: drop the table")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 1
    assert "[inherited] wire" in result.output and "[BREAK] store" in result.output


# --- a declared break on an open master ---------------------------------------------------------


def test_with_an_opening_a_marked_commit_and_a_matching_note_admit_an_undeprecated_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.commit("feat!: remove the widgets route", opening="1", notes={_NOTE_PATH: _note()})
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD")
    assert result.exit_code == 0, result.output
    assert "[allowed]" in result.output


@pytest.mark.parametrize(
    ("message", "opening", "notes"),
    [
        ("feat!: remove the widgets route", "1", {}),
        ("feat: remove the widgets route", "1", {_NOTE_PATH: _note()}),
        ("feat!: remove the widgets route", None, {_NOTE_PATH: _note()}),
        ("feat!: remove the widgets route", "1", {_NOTE_PATH: _note(breaks="/api/fleet/other")}),
        ("feat!: remove the widgets route", "1", {_NOTE_PATH: _note(surfaces="http-api")}),
    ],
    ids=["no-note", "no-marker", "no-opening", "other-name", "other-surface"],
)
def test_an_undeprecated_break_fails_unless_opening_marker_and_note_all_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str, opening: str | None, notes: dict[str, str]
) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.commit(message, opening=opening, notes=notes)
    assert _run(repo, monkeypatch, "--commits", f"{base}..HEAD").exit_code == 1


def test_a_note_already_at_the_base_declares_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.commit("docs: note", opening="1", hub=_spec(route=True), notes={_NOTE_PATH: _note()})
    base = repo.git("rev-parse", "HEAD")
    repo.commit("feat!: remove the widgets route", opening="1")
    assert _run(repo, monkeypatch, "--commits", f"{base}..HEAD").exit_code == 1


# --- tag mode -----------------------------------------------------------------------------------


def _breaks_file(repo: _Repo) -> dict:
    return json.loads((repo.root / "compat-breaks.json").read_text())


def test_a_tag_with_nothing_let_through_writes_an_empty_breaks_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False})
    repo.commit("feat: add things", hub=_spec(route=True))
    result = _run(repo, monkeypatch, "--tag", "0.2.0-rc.2")
    assert result.exit_code == 0, result.output
    assert _breaks_file(repo) == {"tag": "v0.2.0-rc.2", "breaks": []}


def test_a_candidate_above_every_stable_major_admits_a_declared_break_and_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False})
    repo.commit("feat!: remove the widgets route", notes={_NOTE_PATH: _note()})
    result = _run(repo, monkeypatch, "--tag", "0.2.0-rc.2")
    assert result.exit_code == 0, result.output
    assert _breaks_file(repo)["breaks"] == [
        {
            "release": "v0.2.0-rc.1",
            "surface": "wire",
            "where": _ROUTE,
            "reason": "surface path removed",
        }
    ]


def test_a_tag_fails_the_same_break_without_its_note_and_still_writes_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False})
    repo.commit("feat!: remove the widgets route")
    assert _run(repo, monkeypatch, "--tag", "0.2.0-rc.2").exit_code == 1
    assert _breaks_file(repo)["breaks"] == []


def test_a_tag_reads_the_marker_and_note_over_each_releases_own_range(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False})
    repo.commit("feat!: remove the widgets route", notes={_NOTE_PATH: _note()})
    repo.git("tag", "v0.2.0-rc.2")
    repo.commit("feat: more")
    result = _run(repo, monkeypatch, "--tag", "0.2.0-rc.3", "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["releases"] == ["v0.2.0-rc.2"]


def test_a_minor_at_or_below_a_stable_major_admits_only_a_deprecated_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": False, "v0.3.0": True})
    repo.git("tag", "v1.0.0")
    repo.commit("feat!: remove the widgets route", notes={_NOTE_PATH: _note()})
    assert _run(repo, monkeypatch, "--tag", "1.1.0").exit_code == 1


def test_a_major_above_the_newest_stable_admits_a_declared_break(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": False})
    repo.git("tag", "v1.0.0")
    repo.commit("feat!: remove the widgets route", notes={_NOTE_PATH: _note()})
    assert _run(repo, monkeypatch, "--tag", "2.0.0").exit_code == 0


def test_a_patch_admits_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0": True})
    repo.commit("feat!: remove the widgets route", notes={_NOTE_PATH: _note()})
    assert _run(repo, monkeypatch, "--tag", "0.2.1").exit_code == 1


def test_a_tag_excludes_itself_from_the_supported_releases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    _shipped(repo, deprecated_in={"v0.2.0-rc.1": False})
    repo.commit("feat: more")
    repo.git("tag", "v0.2.0-rc.2")
    result = _run(repo, monkeypatch, "--tag", "0.2.0-rc.2", "--json")
    assert json.loads(result.stdout)["releases"] == ["v0.2.0-rc.1"]


@pytest.mark.parametrize("extra", [("--commits", "HEAD~1..HEAD"), ("--line", "release/0.2")])
def test_a_tag_cannot_be_combined_with_a_range_or_a_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: tuple[str, ...]
) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base")
    result = _run(repo, monkeypatch, "--tag", "0.2.0", *extra)
    assert result.exit_code == 2 and "--tag cannot be combined" in result.output


def test_a_malformed_tag_is_a_usage_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    repo.commit("chore: base")
    assert _run(repo, monkeypatch, "--tag", "banana").exit_code == 2


# --- --json -------------------------------------------------------------------------------------


def test_json_emits_every_finding_with_its_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _Repo(tmp_path / "r")
    base = _shipped(repo, deprecated_in={"v0.2.0": True})
    repo.commit("feat: remove the widgets route")
    result = _run(repo, monkeypatch, "--commits", f"{base}..HEAD", "--json")
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {
        "releases": ["v0.2.0"],
        "findings": [
            {
                "release": "v0.2.0",
                "surface": "wire",
                "where": _ROUTE,
                "reason": "surface path removed",
                "deprecated": True,
                "inherited": False,
                "passes": False,
            }
        ],
        "window": [],
    }


# --- the pure decision, with inheritance and declaration -----------------------------------------


def test_a_declared_break_passes_only_when_declarable_and_acknowledged() -> None:
    findings = {"v0.2.0": [_finding(deprecated=False)]}
    declared = {("wire", _ROUTE)}

    def passes(**kwargs: bool) -> bool:
        return gate.judge(findings, line=False, declared=declared, **kwargs)[0].passes  # type: ignore[arg-type]

    assert passes(acknowledged=True, declarable=True)
    assert not passes(acknowledged=True, declarable=False)
    assert not passes(acknowledged=False, declarable=True)


def test_an_inherited_finding_passes_even_on_a_line() -> None:
    inherited = {("v0.2.0", "wire", _ROUTE, "surface path removed")}
    verdict = gate.judge({"v0.2.0": [_finding(deprecated=False)]}, acknowledged=False, line=True, inherited=inherited)[
        0
    ]
    assert verdict.passes and verdict.inherited


def test_a_new_kind_of_break_on_an_inherited_name_is_not_inherited() -> None:
    inherited = {("v0.2.0", "wire", _ROUTE, "an earlier reason")}
    verdict = gate.judge({"v0.2.0": [_finding(deprecated=False)]}, acknowledged=False, line=False, inherited=inherited)[
        0
    ]
    assert not verdict.passes and not verdict.inherited
