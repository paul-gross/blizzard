"""The `bzh:prose-budget` per-block caps: counts the comment and docstring blocks over cap.

`over_cap_blocks(path)` returns one `file:line: ...` string per violation.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

# The `bzh:prose-budget` cap table.
_DOCSTRING_CAPS = {"module": 6, "class": 4, "function": 5, "test": 3}
_CONSTANT_COMMENT_CAP = 1
_INLINE_COMMENT_CAP = 2


def _constant_comment_lines(tree: ast.Module) -> set[int]:
    """First line of every module- or class-body `Assign`/`AnnAssign` — a comment run
    landing immediately above one is a field/column/constant comment, capped at 1 rather
    than an inline block's 2."""
    lines: set[int] = set()
    bodies = [tree.body, *(n.body for n in ast.walk(tree) if isinstance(n, ast.ClassDef))]
    for body in bodies:
        for stmt in body:
            if isinstance(stmt, ast.Assign | ast.AnnAssign):
                lines.add(stmt.lineno)
    return lines


def _docstring_blocks(tree: ast.Module) -> list[tuple[int, str, int]]:
    """Each docstring as (lineno, kind, line count); kind keys `_DOCSTRING_CAPS`."""
    blocks = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                doc = body[0].value
                if isinstance(node, ast.Module):
                    kind = "module"
                elif isinstance(node, ast.ClassDef):
                    kind = "class"
                else:
                    kind = "test" if node.name.startswith("test_") else "function"
                blocks.append((doc.lineno, kind, (doc.end_lineno or doc.lineno) - doc.lineno + 1))
    return blocks


def _comment_runs(src: str) -> list[tuple[int, int]]:
    """Each run of consecutive full-line `#` comments as (start line, line count)."""
    lines = src.splitlines()
    full = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT and lines[tok.start[0] - 1].lstrip().startswith("#"):
                full.add(tok.start[0])
    except tokenize.TokenizeError:
        pass
    runs, start, prev = [], None, None
    for n in sorted(full):
        if start is None or n != prev + 1:
            if start is not None:
                runs.append((start, prev - start + 1))
            start = n
        prev = n
    if start is not None:
        runs.append((start, prev - start + 1))
    return runs


def over_cap_blocks(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    violations = []
    constant_lines: set[int] = set()
    try:
        tree = ast.parse(src)
        constant_lines = _constant_comment_lines(tree)
        for lineno, kind, count in _docstring_blocks(tree):
            cap = _DOCSTRING_CAPS[kind]
            if count > cap:
                violations.append(f"{path}:{lineno}: {kind} docstring {count} lines (cap {cap})")
    except SyntaxError:
        pass
    for lineno, count in _comment_runs(src):
        if (lineno + count) in constant_lines:
            label, cap = "constant comment", _CONSTANT_COMMENT_CAP
        else:
            label, cap = "comment block", _INLINE_COMMENT_CAP
        if count > cap:
            violations.append(f"{path}:{lineno}: {label} {count} lines (cap {cap})")
    return violations
