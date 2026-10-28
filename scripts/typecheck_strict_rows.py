"""Gate only store row typing diagnostics from the strict Pyright report."""

import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

TARGET_RULES = ("reportUnknown", "reportMissingParameterType", "reportArgumentType")
DEFAULT_PATHS = (Path("src/blizzard/runner/store"),)


def _relative(parts: tuple[str, ...], paths: tuple[Path, ...]) -> tuple[str, ...]:
    """The diagnostic's repo-relative parts: from the first part that opens one of the checked paths."""
    for index, part in enumerate(parts):
        if any(part == path.parts[0] and parts[index : index + len(path.parts)] == path.parts for path in paths):
            return parts[index:]
    return parts


def _in_migration_tests(relative: tuple[str, ...]) -> bool:
    return any(relative[i : i + 2] == ("migrations", "tests") for i in range(len(relative) - 1))


def targeted_errors(report: dict[str, Any], paths: tuple[Path, ...] = DEFAULT_PATHS) -> list[dict[str, Any]]:
    summary = report["summary"]
    if type(summary["filesAnalyzed"]) is not int or summary["filesAnalyzed"] < 1:
        raise ValueError("strict report analyzed no files")
    diagnostics = report["generalDiagnostics"]
    if not isinstance(diagnostics, list):
        raise ValueError("strict report has no diagnostics list")
    result = []
    for diagnostic in diagnostics:
        path = Path(diagnostic["file"])
        parts = path.parts
        relative = _relative(parts, paths)
        if (
            diagnostic["severity"] == "error"
            and diagnostic.get("rule", "").startswith(TARGET_RULES)
            and any(relative[: len(path.parts)] == path.parts for path in paths)
            and not _in_migration_tests(relative)
        ):
            result.append(diagnostic)
    return result


def main(argv: Sequence[str] = ()) -> int:
    paths = tuple(Path(argument) for argument in argv) or DEFAULT_PATHS
    command = ["mise", "run", "typecheck-strict", *map(str, paths), "--outputjson"]
    try:
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        report = json.loads(run.stdout)
        errors = targeted_errors(report, paths)
        if run.returncode not in (0, 1):
            raise ValueError(f"strict checker exited {run.returncode}: {run.stderr}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Could not collect strict row diagnostics: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        line = error.get("range", {}).get("start", {}).get("line")
        print(f"{error['file']}:{line + 1 if line is not None else '?'}: {error['rule']}: {error['message']}")
    print(f"{len(errors)} targeted row typing errors; {report['summary']['filesAnalyzed']} files analyzed")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
