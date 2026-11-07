"""Enumerate syntactic candidates and check individually reviewed, scoped inventories."""

import ast
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TypeGuard

from tests.support.stable_ast import unparse

HTTP_NAMES = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "request", "send", "stream"})


@dataclass(frozen=True)
class Candidate:
    file: str
    owner: str
    expression: str
    line: int
    column: int


def candidates(root: Path) -> list[Candidate]:
    """Scan Python files beneath root for HTTP-shaped uses of annotated receivers."""
    result: list[Candidate] = []
    for path in sorted(root.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        visitor = _Candidates(path.relative_to(root).as_posix(), source)
        visitor.visit(ast.parse(source, filename=str(path)))
        result.extend(visitor.found)
    return result


def inventory_changes(found: list[Candidate], reviewed: dict[tuple[str, str, str], str]) -> list[str]:
    """Compare occurrences without line numbers; descriptions record the review, not a proof of behavior."""
    counts = Counter((item.file, item.owner, item.expression) for item in found)
    changes: list[str] = []
    for key in sorted(counts.keys() | reviewed.keys()):
        count = counts[key]
        if count != 1 or not reviewed.get(key, "").strip():
            changes.append(f"{key[0]}: {key[1]}: {key[2]}: found {count}, reviewed {key in reviewed}")
    return changes


class _Candidates(ast.NodeVisitor):
    def __init__(self, file: str, source: str) -> None:
        self.file = file
        self.source = source
        self.found: list[Candidate] = []
        self.owners: list[str] = []
        self.receivers: list[set[str]] = []
        self.fields: list[set[str]] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        fields = {
            statement.target.id
            for statement in node.body
            if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
        }
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                annotated = _annotated_arguments(item.args)
                for child in ast.walk(item):
                    if isinstance(child, ast.AnnAssign) and _self_field(child.target):
                        fields.add(child.target.attr)
                    elif (
                        isinstance(child, ast.Assign)
                        and isinstance(child.value, ast.Name)
                        and child.value.id in annotated
                    ):
                        fields.update(target.attr for target in child.targets if _self_field(target))
        self.owners.append(node.name)
        self.fields.append(fields)
        self.generic_visit(node)
        self.fields.pop()
        self.owners.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.owners.append(node.name)
        self.receivers.append(_annotated_arguments(node.args))
        for statement in node.body:
            self.visit(statement)
        self.receivers.pop()
        self.owners.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node: ast.Assign) -> None:
        self.visit(node.value)
        if self.receivers and self._receiver(node.value):
            self.receivers[-1].update(target.id for target in node.targets if isinstance(target, ast.Name))

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value:
            self.visit(node.value)
        if self.receivers and isinstance(node.target, ast.Name):
            self.receivers[-1].add(node.target.id)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in HTTP_NAMES and self._receiver(node.value):
            self._record(node)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and self._receiver(node.args[0])
            and (not isinstance(node.args[1], ast.Constant) or node.args[1].value in HTTP_NAMES)
        ):
            self._record(node)
        self.generic_visit(node)

    def _receiver(self, node: ast.expr) -> bool:
        return bool(
            (self.receivers and isinstance(node, ast.Name) and node.id in self.receivers[-1])
            or (self.fields and _self_field(node) and node.attr in self.fields[-1])
        )

    def _record(self, node: ast.expr) -> None:
        self.found.append(
            Candidate(
                self.file,
                ".".join(self.owners),
                ast.get_source_segment(self.source, node) or unparse(node),
                node.lineno,
                node.col_offset,
            )
        )


def _self_field(node: ast.expr) -> TypeGuard[ast.Attribute]:
    return isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self"


def _annotated_arguments(args: ast.arguments) -> set[str]:
    arguments = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    arguments.extend(arg for arg in (args.vararg, args.kwarg) if arg is not None)
    return {arg.arg for arg in arguments if arg.annotation is not None}
