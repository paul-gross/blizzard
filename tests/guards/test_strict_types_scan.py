import json
import subprocess
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
RULE = ROOT / "contracts/ast-grep/rules/strict-types.yml"
PATHS = [
    ("conftest.py", True),
    ("scripts/new_tool.py", True),
    ("src/example.py", True),
    ("tests/example.py", True),
    ("exemplars/python/example.py", True),
    ("new_tree/example.py", True),
    ("nested/build/example.py", True),
    ("nested/dist/example.py", True),
    ("web/source/example.py", True),
    ("src/blizzard/static/shared/example.py", True),
    ("build/example.py", False),
    ("dist/example.py", False),
    ("web/dist/example.py", False),
    ("web/tmp/example.py", False),
    ("web/out-tsc/example.py", False),
    ("web/bazel-out/example.py", False),
    ("web/coverage/example.py", False),
    ("web/typings/example.py", False),
    ("src/blizzard/static/hub/example.py", False),
    ("src/blizzard/static/runner/example.py", False),
    ("mutants/example.py", False),
    ("nested/node_modules/example.py", False),
    ("nested/__pycache__/example.py", False),
    (".hidden/example.py", False),
]


@pytest.mark.parametrize(
    ("path", "expected"),
    PATHS,
)
@pytest.mark.parametrize("source", ["value = 1  # type: ignore\n", "# pyright: basic\n", "# pyright: standard\n"])
def test_strict_types_rule_applies_to_real_scans(tmp_path: Path, path: str, expected: bool, source: str) -> None:
    rules = tmp_path / "rules"
    rules.mkdir()
    (rules / "strict-types.yml").write_text(RULE.read_text())
    (tmp_path / "sgconfig.yml").write_text("ruleDirs:\n  - rules\n")
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)

    result = subprocess.run(
        ["ast-grep", "scan", "--json", "--no-ignore", "hidden", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stderr
    matches = json.loads(result.stdout)
    assert bool(matches) is expected
    if expected:
        assert matches[0]["ruleId"] == "bzh:strict-types"


def test_pyright_discovers_the_same_owned_paths(tmp_path: Path) -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["pyright"]
    (tmp_path / "pyrightconfig.json").write_text(json.dumps({key: config[key] for key in ("include", "exclude")}))
    for path, _ in PATHS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('value: int = "wrong"\n')
    result = subprocess.run(["pyright", "--outputjson"], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stderr
    diagnostics = json.loads(result.stdout)["generalDiagnostics"]
    found = {Path(item["file"]).relative_to(tmp_path).as_posix() for item in diagnostics}
    assert found == {path for path, expected in PATHS if expected}
