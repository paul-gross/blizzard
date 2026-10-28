"""The scoped strict gate must distinguish target diagnostics from unrelated errors."""

import json
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from scripts.typecheck_strict_rows import main, targeted_errors


def report(*diagnostics, files=1):
    return {"summary": {"filesAnalyzed": files}, "generalDiagnostics": list(diagnostics)}


@pytest.mark.unit
def test_strict_rows_classifies_category_path_and_severity():
    def diagnostic(file, rule, severity="error"):
        return {"file": file, "rule": rule, "severity": severity, "message": "sample"}

    selected = diagnostic("src/blizzard/runner/store/internal/base.py", "reportUnknownMemberType")
    candidates = (
        selected,
        diagnostic("src/blizzard/runner/store/ask_store.py", "reportMissingParameterType"),
        diagnostic("src/blizzard/runner/store/ask_store.py", "reportArgumentType"),
        diagnostic("src/blizzard/runner/store/ask_store.py", "reportUnusedVariable"),
        diagnostic("src/blizzard/hub/store/ask_store.py", "reportUnknownMemberType"),
        diagnostic("src/blizzard/runner/store/migrations/tests/test_migration.py", "reportArgumentType"),
        diagnostic("src/blizzard/runner/store/ask_store.py", "reportUnknownMemberType", "warning"),
    )
    assert targeted_errors(report(*candidates)) == list(candidates[:3])


@pytest.mark.unit
@pytest.mark.parametrize("output,code", [(report(files=0), 0), ("not json", 1), (report(), 2)])
def test_strict_rows_refuses_invalid_collection(output, code):
    stdout = output if isinstance(output, str) else json.dumps(output)
    with patch("scripts.typecheck_strict_rows.subprocess.run", return_value=CompletedProcess([], code, stdout, "")):
        assert main([]) == 1


@pytest.mark.unit
def test_strict_rows_accepts_unrelated_strict_errors():
    output = report(
        {"file": "src/blizzard/runner/store/ask_store.py", "rule": "reportUnusedVariable", "severity": "error"}
    )
    with patch(
        "scripts.typecheck_strict_rows.subprocess.run", return_value=CompletedProcess([], 1, json.dumps(output), "")
    ):
        assert main([]) == 0


@pytest.mark.unit
def test_strict_rows_fails_on_target_error():
    output = report(
        {
            "file": "src/blizzard/runner/store/ask_store.py",
            "rule": "reportArgumentType",
            "severity": "error",
            "message": "bad",
        }
    )
    with patch(
        "scripts.typecheck_strict_rows.subprocess.run", return_value=CompletedProcess([], 1, json.dumps(output), "")
    ):
        assert main([]) == 1


@pytest.mark.unit
def test_strict_rows_selects_passed_paths_and_excludes_their_migration_tests():
    def diagnostic(file):
        return {"file": file, "rule": "reportUnknownMemberType", "severity": "error", "message": "sample"}

    hub = diagnostic("/work/blizzard/src/blizzard/hub/store/internal/newest_fact.py")
    exemplar = diagnostic("/work/blizzard/exemplars/python/exemplar/hub/store/internal/permit_store.py")
    candidates = (
        hub,
        exemplar,
        diagnostic("/work/blizzard/src/blizzard/hub/store/migrations/tests/test_migration.py"),
        diagnostic("/work/blizzard/src/blizzard/runner/store/ask_store.py"),
        diagnostic("/work/blizzard/exemplars/python/tests/test_permit_service.py"),
    )
    paths = (
        Path("src/blizzard/hub/store"),
        Path("exemplars/python/exemplar/hub/store/internal/permit_store.py"),
    )
    assert targeted_errors(report(*candidates), paths) == [hub, exemplar]
