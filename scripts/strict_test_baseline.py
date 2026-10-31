"""Reconcile strict Pyright JSON reports across the workspace's Python projects."""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

MIN_FILES = {"blizzard": 2000, "blizzard-mock": 200, "blizzard-infra": 3}


def area(repo: str, path: Path) -> str:
    parts = path.parts
    if repo == "blizzard":
        if path.is_relative_to("src") and "migrations" in parts and "tests" in parts:
            return "store-migration-tests"
        if path.is_relative_to("exemplars/python"):
            return "exemplar-tests" if "tests" in parts else "exemplar-shipping"
        if path.is_relative_to("tests"):
            return f"tests/{parts[1]}" if len(parts) > 2 else "tests/root"
        if path.is_relative_to("src"):
            return "shipping"
    elif repo == "blizzard-mock":
        if path.is_relative_to("tests"):
            return "tests"
        if path.is_relative_to("src"):
            return "shipping"
    elif repo == "blizzard-infra" and path.is_relative_to("tests"):
        return "tests"
    raise ValueError(f"out-of-scope diagnostic: {repo}/{path}")


def cause(rule: str) -> str:
    """Classify a symptom once; unknown propagation still needs root inspection."""
    if rule == "reportPrivateUsage":
        return "private API reach"
    if rule in {"reportMissingParameterType", "reportUnknownParameterType"}:
        return "untyped signature/fixture root"
    if rule == "reportMissingTypeArgument":
        return "bare generic"
    if rule.startswith("reportUnknown"):
        return "unknown propagation (root to inspect)"
    if rule == "reportArgumentType":
        return "argument contract mismatch (root to inspect)"
    return "other rule-specific root to inspect"


def reconcile(repo: str, root: Path, report: dict[str, Any]) -> tuple[Counter[str], dict[str, set[str]], Counter[str]]:
    """Assign every error once to an area, rule and cause; reject empty reports."""
    summary = report["summary"]
    diagnostics = report["generalDiagnostics"]
    if summary["filesAnalyzed"] < MIN_FILES[repo] or not isinstance(diagnostics, list):
        raise ValueError(f"{repo}: vacuous report ({summary['filesAnalyzed']} analyzed files)")
    if summary["errorCount"] != len(diagnostics) or not diagnostics:
        raise ValueError(f"{repo}: error summary does not match nonempty diagnostic list")
    counts: Counter[str] = Counter()
    files: dict[str, set[str]] = defaultdict(set)
    causes: Counter[str] = Counter()
    for diagnostic in diagnostics:
        if diagnostic["severity"] != "error" or not diagnostic.get("rule"):
            raise ValueError(f"{repo}: diagnostic without an error severity and rule")
        path = Path(diagnostic["file"]).resolve().relative_to(root.resolve())
        group = area(repo, path)
        counts[group] += 1
        files[group].add(str(path))
        causes[cause(diagnostic["rule"])] += 1
    if sum(counts.values()) != summary["errorCount"] or sum(causes.values()) != summary["errorCount"]:
        raise ValueError(f"{repo}: unreconciled diagnostics")
    return counts, files, causes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path.cwd().parent)
    for repo in MIN_FILES:
        parser.add_argument(f"--{repo}", type=Path, required=True, metavar="PYRIGHT_JSON")
    args = parser.parse_args()
    for repo in MIN_FILES:
        report = json.loads(getattr(args, repo.replace("-", "_")).read_text())
        counts, files, causes = reconcile(repo, args.workspace / repo, report)
        print(f"{repo}: {sum(counts.values())} errors, {report['summary']['filesAnalyzed']} files analyzed")
        for group, count in sorted(counts.items()):
            print(f"  {group}: {count} errors, {len(files[group])} files with errors")
        print(f"  cause categories: {dict(sorted(causes.items()))}")


if __name__ == "__main__":
    main()
