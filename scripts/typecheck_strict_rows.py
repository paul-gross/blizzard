"""Gate only runner-store row typing diagnostics from the strict Pyright report."""

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

TARGET_RULES = ("reportUnknown", "reportMissingParameterType", "reportArgumentType")
STORE = Path("src/blizzard/runner/store")


def targeted_errors(report: dict[str, Any]) -> list[dict[str, Any]]:
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
        relative = parts[parts.index("src") :] if "src" in parts else parts
        if (
            diagnostic["severity"] == "error"
            and diagnostic.get("rule", "").startswith(TARGET_RULES)
            and relative[: len(STORE.parts)] == STORE.parts
            and relative[len(STORE.parts) : len(STORE.parts) + 2] != ("migrations", "tests")
        ):
            result.append(diagnostic)
    return result


def main() -> int:
    command = ["mise", "run", "typecheck-strict", str(STORE), "--outputjson"]
    try:
        run = subprocess.run(command, capture_output=True, text=True, check=False)
        report = json.loads(run.stdout)
        errors = targeted_errors(report)
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
    sys.exit(main())
