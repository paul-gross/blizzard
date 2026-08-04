"""Comment/docstring density ratchet (issue #270, `bzh:comment-locality`).

Measures per-file `#`-comment and docstring line counts over Python trees and
compares them against the recorded baseline so prose growth is reportable.

Measure:            python scripts/prose_density.py measure src tests ../blizzard-mock/src
Record baseline:    python scripts/prose_density.py measure --write-baseline src tests ../blizzard-mock/src
Report growth:      python scripts/prose_density.py check src tests ../blizzard-mock/src

`check` exits 1 when any measured root's total prose exceeds its baseline.
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
    args = parser.parse_args()

    report = {root: _measure_root(Path(root)) for root in args.roots}
    for root, files in report.items():
        _print_root(root, _aggregate(files))

    if args.command == "measure":
        if args.write_baseline:
            BASELINE.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n")
            print(f"baseline written: {BASELINE}")
        return 0

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
    return 1 if grew else 0


if __name__ == "__main__":
    sys.exit(main())
