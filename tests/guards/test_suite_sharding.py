"""``BLIZZARD_TEST_SHARD`` splits the suite into disjoint shards that together cover it — by test after collection,
or by test module before import under ``BLIZZARD_TEST_SHARD_BY=file``."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

import pytest

from tests.support.repo_files import repo_root
from tests.support.suite_plugin import parse_shard, pytest_ignore_collect, shard_by, shard_of

_NODEIDS = [f"tests/test_mod_{m}.py::test_case[{p}]" for m in range(40) for p in range(25)]


def test_every_test_lands_in_exactly_one_shard() -> None:
    shards = [shard_of(nodeid, 4) for nodeid in _NODEIDS]
    assert set(shards) == {1, 2, 3, 4}


def test_shards_split_a_large_suite_roughly_evenly() -> None:
    sizes = [sum(1 for nodeid in _NODEIDS if shard_of(nodeid, 4) == i) for i in (1, 2, 3, 4)]
    assert min(sizes) > len(_NODEIDS) / 4 * 0.8


def test_one_shard_keeps_everything() -> None:
    assert {shard_of(nodeid, 1) for nodeid in _NODEIDS} == {1}


@pytest.mark.parametrize(("spec", "expected"), [("1/4", (1, 4)), ("4/4", (4, 4)), ("1/1", (1, 1))])
def test_parse_shard_reads_index_and_count(spec: str, expected: tuple[int, int]) -> None:
    assert parse_shard(spec) == expected


@pytest.mark.parametrize("spec", ["0/4", "5/4", "4", "a/4", "1/", "/4", "-1/4", "1/0"])
def test_parse_shard_rejects_a_malformed_spec(spec: str) -> None:
    with pytest.raises(pytest.UsageError):
        parse_shard(spec)


def test_the_split_unit_defaults_to_test() -> None:
    assert shard_by({}) == "test"
    assert shard_by({"BLIZZARD_TEST_SHARD_BY": ""}) == "test"


@pytest.mark.parametrize("unit", ["test", "file"])
def test_the_split_unit_reads_test_or_file(unit: str) -> None:
    assert shard_by({"BLIZZARD_TEST_SHARD_BY": unit}) == unit


def test_an_unknown_split_unit_is_a_usage_error() -> None:
    with pytest.raises(pytest.UsageError, match="BLIZZARD_TEST_SHARD_BY='module'"):
        shard_by({"BLIZZARD_TEST_SHARD_BY": "module"})


class _Config:
    """The slice of pytest's config the file split reads."""

    def __init__(self, rootpath: Path) -> None:
        self.rootpath = rootpath

    def getini(self, name: str) -> list[str]:
        assert name == "python_files"
        return ["test_*.py", "*_test.py"]


def _modules(root: Path) -> list[Path]:
    paths = [root / "tests" / f"test_mod_{m}.py" for m in range(40)] + [
        root / "src" / f"m_{m}_test.py" for m in range(8)
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    return paths


def _ignored(path: Path, root: Path) -> bool | None:
    return pytest_ignore_collect(path, cast("pytest.Config", _Config(root)))


def _file_split(monkeypatch: pytest.MonkeyPatch, spec: str) -> None:
    monkeypatch.setenv("BLIZZARD_TEST_SHARD", spec)
    monkeypatch.setenv("BLIZZARD_TEST_SHARD_BY", "file")


def test_the_file_split_keeps_each_test_module_in_exactly_one_shard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    modules = _modules(tmp_path)
    kept: list[Path] = []
    for index in (1, 2, 3, 4):
        _file_split(monkeypatch, f"{index}/4")
        shard = [path for path in modules if _ignored(path, tmp_path) is None]
        assert shard, f"shard {index}/4 kept no module"
        kept += shard
    assert sorted(kept) == sorted(modules)


def test_the_file_split_skips_a_module_by_its_repo_relative_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _modules(tmp_path)[0]
    own = shard_of("tests/test_mod_0.py", 4)
    _file_split(monkeypatch, f"{own}/4")
    assert _ignored(module, tmp_path) is None
    _file_split(monkeypatch, f"{own % 4 + 1}/4")
    assert _ignored(module, tmp_path) is True


@pytest.mark.parametrize("name", ["conftest.py", "__init__.py", "helpers.py"])
def test_the_file_split_leaves_a_non_test_module_to_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    path = tmp_path / "tests" / name
    path.parent.mkdir(parents=True)
    path.write_text("")
    for index in (1, 2, 3, 4):
        _file_split(monkeypatch, f"{index}/4")
        assert _ignored(path, tmp_path) is None


def test_a_directory_is_left_to_pytest_so_its_modules_are_judged_one_by_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "test_dir.py"
    folder.mkdir()
    for index in (1, 2, 3, 4):
        _file_split(monkeypatch, f"{index}/4")
        assert _ignored(folder, tmp_path) is None


@pytest.mark.parametrize("unit", ["", "test"])
def test_the_test_split_imports_every_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unit: str) -> None:
    monkeypatch.setenv("BLIZZARD_TEST_SHARD", "1/4")
    monkeypatch.setenv("BLIZZARD_TEST_SHARD_BY", unit)
    assert all(_ignored(path, tmp_path) is None for path in _modules(tmp_path))


def test_an_unsharded_run_imports_every_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BLIZZARD_TEST_SHARD", raising=False)
    monkeypatch.setenv("BLIZZARD_TEST_SHARD_BY", "file")
    assert all(_ignored(path, tmp_path) is None for path in _modules(tmp_path))


#: A folder of several test modules, small enough that each collect-only session costs little more than its startup.
_SPLIT_FOLDER = "tests/unit/runner/lifecycle"


def _nodeids(shard: dict[str, str]) -> set[str]:
    environ = {name: value for name, value in os.environ.items() if not name.startswith("BLIZZARD_TEST_SHARD")}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", _SPLIT_FOLDER],
        cwd=repo_root(),
        env=environ | shard,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return {line for line in result.stdout.splitlines() if "::" in line}


def test_a_real_session_splits_by_file_into_disjoint_shards_covering_the_unsharded_run() -> None:
    shards = [{"BLIZZARD_TEST_SHARD": f"{index}/2", "BLIZZARD_TEST_SHARD_BY": "file"} for index in (1, 2)]
    with ThreadPoolExecutor() as pool:
        first, second, unsharded = pool.map(_nodeids, [*shards, {}])
    assert first and second
    assert not first & second
    assert first | second == unsharded
