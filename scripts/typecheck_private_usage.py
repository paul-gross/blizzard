"""Gate private-usage diagnostics in shipping Python from the strict Pyright report."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

DEFAULT_PATHS = (Path("src"), Path("exemplars/python"))


def targeted_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
    summary = report["summary"]
    if type(summary["filesAnalyzed"]) is not int or summary["filesAnalyzed"] < 1:
        raise ValueError("strict report analyzed no files")
    diagnostics = report["generalDiagnostics"]
    if not isinstance(diagnostics, list):
        raise ValueError("strict report has no diagnostics list")
    selected = []
    for diagnostic in diagnostics:
        parts = Path(diagnostic["file"]).parts
        for index, part in enumerate(parts):
            if part not in ("src", "exemplars"):
                continue
            relative = parts[index:]
            if not any(relative[: len(path.parts)] == path.parts for path in DEFAULT_PATHS):
                continue
            if "tests" in relative or any(
                relative[i : i + 3] == ("store", "migrations", "tests") for i in range(len(relative) - 2)
            ):
                break
            if diagnostic["severity"] == "error" and diagnostic.get("rule") == "reportPrivateUsage":
                selected.append(diagnostic)
            break
    return selected


def main() -> int:
    command = ["mise", "run", "typecheck-strict", *map(str, DEFAULT_PATHS), "--outputjson"]
    try:
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        report = json.loads(run.stdout)
        errors = targeted_errors(report)
        if run.returncode not in (0, 1):
            raise ValueError(f"strict checker exited {run.returncode}: {run.stderr}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Could not collect strict private-usage diagnostics: {exc}", file=sys.stderr)
        return 1
    for error in errors:
        line = error.get("range", {}).get("start", {}).get("line")
        print(f"{error['file']}:{line + 1 if line is not None else '?'}: {error['rule']}: {error['message']}")
    print(f"{len(errors)} shipping private-usage errors; {report['summary']['filesAnalyzed']} files analyzed")
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
