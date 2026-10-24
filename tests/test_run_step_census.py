"""No test authors a hub node naming ``run:`` — as a mapping literal or as YAML in a string — off the allowlist.

Mint refuses ``run:``, so a test authoring it is about a stored version minted before hub nodes named
actions, or about a hub that cannot read ``action:``; every other test names an action."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

#: The files, relative to ``tests/``, whose subject is a stored pre-action document or a hub that cannot read
#: ``action:``.
RUN_STEP_ALLOWLIST = frozenset(
    {
        "rehearsal/test_upgrade_rehearsal.py",
        "test_legacy_run.py",
    }
)


def _is_step_list(value: object) -> bool:
    return isinstance(value, list) and any(isinstance(step, dict) and "command" in step for step in value)


def _yaml_authors_run(document: object) -> bool:
    if isinstance(document, dict):
        if "run" in document and ("executor" in document or _is_step_list(document["run"])):
            return True
        return any(_yaml_authors_run(value) for value in document.values())
    if isinstance(document, list):
        return any(_yaml_authors_run(item) for item in document)
    return False


def _string_authors_run(text: str) -> bool:
    if "run:" not in text:
        return False
    try:
        documents = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return False
    return any(_yaml_authors_run(document) for document in documents)


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _dict_authors_run(node: ast.Dict) -> bool:
    keys = {_literal(key): value for key, value in zip(node.keys, node.values, strict=True)}
    if "run" not in keys:
        return False
    if "executor" in keys:
        return True
    value = keys["run"]
    return isinstance(value, ast.List) and any(
        isinstance(step, ast.Dict) and "command" in {_literal(key) for key in step.keys} for step in value.elts
    )


def _string_constants(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            yield "".join(_literal(part) or "x" for part in node.values)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


def authors_run_step(source: str) -> bool:
    tree = ast.parse(source)
    if any(isinstance(node, ast.Dict) and _dict_authors_run(node) for node in ast.walk(tree)):
        return True
    return any(_string_authors_run(text) for text in _string_constants(tree))


def run_step_authors(root: Path) -> set[str]:
    return {
        path.relative_to(root).as_posix() for path in sorted(root.rglob("*.py")) if authors_run_step(path.read_text())
    }


def test_only_the_allowlist_authors_a_run_step() -> None:
    found = run_step_authors(repo_root() / "tests")
    assert found - RUN_STEP_ALLOWLIST == set(), "these test files author a hub node naming `run:`; name an action"
    assert RUN_STEP_ALLOWLIST - found == set(), "these allowlisted files no longer author `run:`; drop them"


@pytest.mark.parametrize(
    "source",
    [
        'NODE = {"executor": "hub", "run": [{"command": "true"}]}\n',
        'STEPS = {"run": [{"name": "land", "command": "true"}]}\n',
        'GRAPH = """\nnodes:\n  deliver:\n    executor: hub\n    run:\n      - command: "true"\n"""\n',
        'GRAPH = f"""\nnodes:\n  {"deliver"}:\n    executor: hub\n    run: [{{command: "true"}}]\n"""\n',
    ],
    ids=["dict", "step-list", "yaml", "f-string-yaml"],
)
def test_a_planted_run_step_outside_the_allowlist_is_found(tmp_path: Path, source: str) -> None:
    (tmp_path / "test_planted.py").write_text(source)
    (tmp_path / "rehearsal").mkdir()
    (tmp_path / "rehearsal" / "test_upgrade_rehearsal.py").write_text(source)

    assert run_step_authors(tmp_path) - RUN_STEP_ALLOWLIST == {"test_planted.py"}


@pytest.mark.parametrize(
    "source",
    [
        'NODE = {"executor": "hub", "action": "pass"}\n',
        'ARGS = {"run": "a subcommand name"}\n',
        'TEXT = "run: a sentence, not a graph"\n',
        "def run(run: int) -> int:\n    return run\n",
    ],
    ids=["action", "unrelated-key", "prose", "identifier"],
)
def test_a_file_naming_no_run_step_is_not_found(source: str) -> None:
    assert not authors_run_step(source)
