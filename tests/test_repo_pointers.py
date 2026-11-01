from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.repo_files import repo_root

pytestmark = pytest.mark.component

_SCRIPTS = repo_root() / "scripts"


def _load_module(name: str, filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load_module("prose_spans", "prose_spans.py")
rp = _load_module("repo_pointers", "repo_pointers.py")


def _repo(tmp_path: Path, files: dict[str, str], *, untracked: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    for rel, text in (untracked or {}).items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


def _exemptions(tmp_path: Path, entries: list[dict[str, object]] | None = None) -> Path:
    path = tmp_path / "exemptions.json"
    path.write_text(json.dumps(entries or []))
    return path


def _entry(target: str, count: int = 1, *, file: str = "src/a.py", symbol: str = "<module>") -> dict[str, object]:
    return {"file": file, "symbol": symbol, "target": target, "count": count, "kind": "generated", "reason": "r"}


def _check(tmp_path: Path, doc: str, *, extra: dict[str, str] | None = None, **kw) -> list[str]:
    files = {"src/a.py": f'"""{doc}"""\n', "src/real.py": "", "docs/guide/x.md": "", **(extra or {})}
    root = _repo(tmp_path, files, untracked=kw.pop("untracked", None))
    return rp.check(root, _exemptions(tmp_path, kw.pop("entries", None)))


def test_a_pointer_at_a_tracked_file_passes(tmp_path: Path) -> None:
    assert _check(tmp_path, "See `src/real.py`.") == []


def test_a_pointer_at_a_missing_file_fails_naming_site_and_target(tmp_path: Path) -> None:
    problems = _check(tmp_path, "See src/gone.py for details.")
    assert problems == ["src/a.py:1: <module>: 'src/gone.py' is not a tracked path"]


def test_the_deleted_migration_lineage_shape_fails(tmp_path: Path) -> None:
    problems = _check(tmp_path, "Seeds like ``tests/test_x_migration.py`` did.")
    assert len(problems) == 1 and "'tests/test_x_migration.py'" in problems[0]


def test_a_directory_pointer_needs_tracked_descendants(tmp_path: Path) -> None:
    assert _check(tmp_path, "Under docs/guide/ and docs/guide.") == []
    assert len(_check(tmp_path / "b", "Under docs/empty/.", untracked={"docs/empty/x.md": ""})) == 1


def test_an_untracked_file_on_disk_never_satisfies_a_pointer(tmp_path: Path) -> None:
    problems = _check(tmp_path, "See src/local.py.", untracked={"src/local.py": ""})
    assert len(problems) == 1 and "'src/local.py'" in problems[0]


@pytest.mark.parametrize(
    "doc",
    [
        "https://example.com/src/gone.py",
        "blizzard-context:/src/gone.py",
        "blizzard-mock/src/gone.py",
        "../blizzard-mock/tests/gone.py",
        "the_src/gone.py",
        "a.src/gone.py",
    ],
)
def test_urls_notation_and_cross_repo_prefixes_are_not_checked(tmp_path: Path, doc: str) -> None:
    assert _check(tmp_path, doc) == []


@pytest.mark.parametrize(
    "doc",
    [
        "src/real.py:12",
        "src/real.py:12-30.",
        "src/real.py#anchor",
        "src/real.py::Thing",
        "src/real.py.Thing.method,",
        "'src/real.py'",
        '"src/real.py"!',
        "(src/real.py)",
        "src/real.py:thing",
    ],
)
def test_suffixes_and_punctuation_are_stripped_to_the_file(tmp_path: Path, doc: str) -> None:
    assert _check(tmp_path, doc) == []


@pytest.mark.parametrize(
    ("doc", "ok"),
    [
        ("src/*.py", True),
        ("src/**/x.md", False),
        ("docs/**/x.md", True),
        ("src/{real,a}.py", True),
        ("src/{gone,nope}.py", False),
        ("src/rea?.py", True),
        ("docs/<section>/x.md", True),
        ("docs/<section>/y.md", False),
    ],
)
def test_globs_and_templates_pass_on_any_tracked_match(tmp_path: Path, doc: str, ok: bool) -> None:
    assert (_check(tmp_path, doc) == []) is ok


def test_a_template_prefix_is_never_checked_alone(tmp_path: Path) -> None:
    assert len(_check(tmp_path, "docs/<a>/<b>/<c>/x.md")) == 1


def test_trailing_inline_comments_are_scanned(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/a.py": "x = 1  # see src/gone.py\n"})
    assert rp.check(root, _exemptions(tmp_path)) == ["src/a.py:1: <module>: 'src/gone.py' is not a tracked path"]


def test_string_literals_are_not_prose(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"src/a.py": 'X = "src/gone.py"\n'})
    assert rp.check(root, _exemptions(tmp_path)) == []


def test_files_outside_the_scanned_roots_are_ignored(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"docs/a.py": '"""src/gone.py"""\n', "web/a.py": "# src/gone.py\n"})
    assert rp.check(root, _exemptions(tmp_path)) == []


def test_an_exemption_honours_its_exact_count(tmp_path: Path) -> None:
    assert _check(tmp_path, "src/gone.py", entries=[_entry("src/gone.py")]) == []


def test_occurrences_over_the_exemption_count_fail(tmp_path: Path) -> None:
    problems = _check(tmp_path, "src/gone.py and src/gone.py", entries=[_entry("src/gone.py")])
    assert problems == ["src/a.py: <module>: 'src/gone.py' occurs 2 times, over its exemption's 1"]


def test_an_exemption_counting_more_than_observed_is_stale(tmp_path: Path) -> None:
    problems = _check(tmp_path, "src/gone.py", entries=[_entry("src/gone.py", 2)])
    assert len(problems) == 1 and "stale" in problems[0]


def test_an_exemption_whose_target_now_resolves_or_vanished_is_stale(tmp_path: Path) -> None:
    problems = _check(tmp_path, "src/real.py", entries=[_entry("src/real.py"), _entry("src/elsewhere.py")])
    assert len(problems) == 2 and all("stale" in p for p in problems)


def test_an_exemption_for_another_site_does_not_cover_this_one(tmp_path: Path) -> None:
    problems = _check(tmp_path, "src/gone.py", entries=[_entry("src/gone.py", symbol="other")])
    assert any("is not a tracked path" in p for p in problems) and any("stale" in p for p in problems)


@pytest.mark.parametrize(
    "entry",
    [
        {**_entry("src/gone.py"), "kind": "wildcard"},
        {**_entry("src/gone.py"), "reason": " "},
        {**_entry("src/gone.py"), "count": 0},
        {k: v for k, v in _entry("src/gone.py").items() if k != "reason"},
    ],
)
def test_a_malformed_exemption_is_an_error(tmp_path: Path, entry: dict[str, object]) -> None:
    with pytest.raises(rp.PointerCheckError):
        _check(tmp_path, "src/gone.py", entries=[entry])


def test_a_git_failure_is_an_error_naming_the_root(tmp_path: Path) -> None:
    root = tmp_path / "broken"
    root.mkdir()
    (root / ".git").write_text("gitdir: /nonexistent\n")
    with pytest.raises(rp.PointerCheckError, match="git ls-files failed"):
        rp.check(root, _exemptions(tmp_path))


def test_an_unparseable_file_is_an_error_naming_it(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"tests/bad.py": "def (:\n"})
    with pytest.raises(rp.PointerCheckError, match=r"tests/bad\.py"):
        rp.check(root, _exemptions(tmp_path))
