"""The baseline checker rejects a vacuous or misassigned strict report."""

from pathlib import Path

import pytest

from scripts.strict_test_baseline import reconcile


def _report(files: int, diagnostics: list[dict[str, str]]) -> dict:
    return {"summary": {"filesAnalyzed": files, "errorCount": len(diagnostics)}, "generalDiagnostics": diagnostics}


def test_reconciles_each_diagnostic_to_one_area(tmp_path: Path) -> None:
    root = tmp_path / "blizzard-infra"
    root.mkdir()
    report = _report(3, [{"file": str(root / "tests/test_pin.py"), "severity": "error", "rule": "reportArgumentType"}])
    counts, files, causes = reconcile("blizzard-infra", root, report, expected=(3, {"tests": 1}))
    assert counts == {"tests": 1}
    assert files == {"tests": {"tests/test_pin.py"}}
    assert causes == {"argument contract mismatch (root to inspect)": 1}


@pytest.mark.parametrize("files", [0, 2])
def test_refuses_vacuous_file_scope(tmp_path: Path, files: int) -> None:
    report = _report(files, [])
    with pytest.raises(ValueError):
        reconcile("blizzard-infra", tmp_path, report)


def test_refuses_truncated_full_scope_report(tmp_path: Path) -> None:
    report = _report(
        2000, [{"file": str(tmp_path / "tests/test_pin.py"), "severity": "error", "rule": "reportArgumentType"}]
    )
    with pytest.raises(ValueError, match="vacuous report"):
        reconcile("blizzard", tmp_path, report)


def test_refuses_out_of_scope_diagnostic(tmp_path: Path) -> None:
    report = _report(3, [{"file": str(tmp_path / "elsewhere.py"), "severity": "error", "rule": "reportArgumentType"}])
    with pytest.raises(ValueError):
        reconcile("blizzard-infra", tmp_path / "blizzard-infra", report)


def test_refuses_inconsistent_summary(tmp_path: Path) -> None:
    report = _report(
        3, [{"file": str(tmp_path / "tests/test_pin.py"), "severity": "error", "rule": "reportArgumentType"}]
    )
    report["summary"]["errorCount"] = 2
    with pytest.raises(ValueError):
        reconcile("blizzard-infra", tmp_path, report)


def test_refuses_undercount_even_with_full_file_scope(tmp_path: Path) -> None:
    report = _report(
        3, [{"file": str(tmp_path / "tests/test_pin.py"), "severity": "error", "rule": "reportArgumentType"}]
    )
    with pytest.raises(ValueError, match="diagnostic areas differ"):
        reconcile("blizzard-infra", tmp_path, report)
