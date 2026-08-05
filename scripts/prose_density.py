"""Comment/docstring density: per-root growth ratchet + per-block caps (issue #270).

measure [--write-baseline] ROOTS...  report (or record) per-root prose totals.
check ROOTS...                       exit 1 when a root's prose grows over the baseline.
check --blocks ROOTS...              additionally exit 1 on any block over its
`bzh:prose-budget` cap, each named as file:line.
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
from pathlib import Path

BASELINE = Path(__file__).with_name("prose-density-baseline.json")


def _docstring_lines(tree: ast.Module) -> int:
    total = 0
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
                total += (doc.end_lineno or doc.lineno) - doc.lineno + 1
    return total


# The `bzh:prose-budget` cap table.
_DOCSTRING_CAPS = {"module": 6, "class": 4, "function": 5, "test": 3}
_COMMENT_RUN_CAP = 2


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


def _over_cap_blocks(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8")
    violations = []
    try:
        for lineno, kind, count in _docstring_blocks(ast.parse(src)):
            cap = _DOCSTRING_CAPS[kind]
            if count > cap:
                violations.append(f"{path}:{lineno}: {kind} docstring {count} lines (cap {cap})")
    except SyntaxError:
        pass
    for lineno, count in _comment_runs(src):
        if count > _COMMENT_RUN_CAP:
            violations.append(f"{path}:{lineno}: comment block {count} lines (cap {_COMMENT_RUN_CAP})")
    return violations


def _measure_file(path: Path) -> dict[str, int]:
    src = path.read_text(encoding="utf-8")
    total = src.count("\n") + (0 if src.endswith("\n") or not src else 1)
    comment_lines: set[int] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                comment_lines.add(tok.start[0])
    except tokenize.TokenizeError:
        pass
    try:
        docstrings = _docstring_lines(ast.parse(src))
    except SyntaxError:
        docstrings = 0
    return {"total": total, "comments": len(comment_lines), "docstrings": docstrings}


def _measure_root(root: Path) -> dict[str, dict[str, int]]:
    return {str(f.relative_to(root)): _measure_file(f) for f in sorted(root.rglob("*.py"))}


def _aggregate(files: dict[str, dict[str, int]]) -> dict[str, int]:
    agg = {"total": 0, "comments": 0, "docstrings": 0}
    for m in files.values():
        for key in agg:
            agg[key] += m[key]
    agg["prose"] = agg["comments"] + agg["docstrings"]
    return agg


def _print_root(name: str, agg: dict[str, int]) -> None:
    pct = 100.0 * agg["prose"] / agg["total"] if agg["total"] else 0.0
    print(
        f"{name}: total={agg['total']} comments={agg['comments']} "
        f"docstrings={agg['docstrings']} prose={agg['prose']} ({pct:.1f}%)"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["measure", "check"])
    parser.add_argument("roots", nargs="+")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--blocks", action="store_true")
    args = parser.parse_args()

    report = {root: _measure_root(Path(root)) for root in args.roots}
    for root, files in report.items():
        _print_root(root, _aggregate(files))

    if args.command == "measure":
        if args.write_baseline:
            BASELINE.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
            print(f"baseline written: {BASELINE}")
        return 0

    over_cap = False
    if args.blocks:
        for root in args.roots:
            for f in sorted(Path(root).rglob("*.py")):
                for violation in _over_cap_blocks(f):
                    print(violation)
                    over_cap = True

    baseline = json.loads(BASELINE.read_text())
    grew = False
    for root, files in report.items():
        base_files = baseline.get(root)
        if base_files is None:
            print(f"{root}: no baseline recorded — run measure --write-baseline")
            grew = True
            continue
        agg, base_agg = _aggregate(files), _aggregate(base_files)
        delta = agg["prose"] - base_agg["prose"]
        print(f"{root}: prose {base_agg['prose']} -> {agg['prose']} ({delta:+d})")
        if delta > 0:
            grew = True
            for name, m in files.items():
                base_prose = sum(base_files.get(name, {}).get(k, 0) for k in ("comments", "docstrings"))
                file_delta = m["comments"] + m["docstrings"] - base_prose
                if file_delta > 0:
                    print(f"  +{file_delta} {name}")
    return 1 if grew or over_cap else 0


if __name__ == "__main__":
    sys.exit(main())
