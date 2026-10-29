"""The strict private-usage gate includes shipping revisions but not test trees."""

import json
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from scripts.typecheck_private_usage import main, targeted_errors


def report(*diagnostics, files=1):
    return {"summary": {"filesAnalyzed": files}, "generalDiagnostics": list(diagnostics)}


def diagnostic(file, rule="reportPrivateUsage", severity="error"):
    return {"file": file, "rule": rule, "severity": severity, "message": "sample"}


@pytest.mark.unit
def test_shipping_private_usage_includes_historical_revision_and_exemplar():
    planted = diagnostic("/work/blizzard/src/blizzard/hub/api/host.py")
    revision = diagnostic("src/blizzard/runner/store/migrations/versions/20261006_1000_runner_identity.py")
    exemplar = diagnostic("/work/blizzard/exemplars/python/exemplar/permit.py")
    candidates = (
        planted,
        revision,
        exemplar,
        diagnostic("tests/test_host.py"),
        diagnostic("src/blizzard/runner/store/migrations/tests/test_revision.py"),
        diagnostic("exemplars/python/tests/test_permit.py"),
        diagnostic("src/blizzard/hub/api/host.py", "reportUnknownMemberType"),
        diagnostic("src/blizzard/hub/api/host.py", severity="warning"),
    )
    assert targeted_errors(report(*candidates)) == [planted, revision, exemplar]


@pytest.mark.unit
@pytest.mark.parametrize("output,code", [(report(files=0), 0), ("not json", 1), (report(), 2)])
def test_invalid_collection_fails(output, code):
    stdout = output if isinstance(output, str) else json.dumps(output)
    with patch("scripts.typecheck_private_usage.subprocess.run", return_value=CompletedProcess([], code, stdout, "")):
        assert main() == 1


@pytest.mark.unit
def test_unrelated_strict_failure_does_not_fail_gate():
    output = report(diagnostic("src/blizzard/hub/api/host.py", "reportUnknownMemberType"))
    with patch(
        "scripts.typecheck_private_usage.subprocess.run", return_value=CompletedProcess([], 1, json.dumps(output), "")
    ):
        assert main() == 0


@pytest.mark.unit
def test_shipping_private_usage_fails_gate():
    output = report(diagnostic("src/blizzard/runner/store/migrations/versions/20261006_1000_runner_identity.py"))
    with patch(
        "scripts.typecheck_private_usage.subprocess.run", return_value=CompletedProcess([], 1, json.dumps(output), "")
    ):
        assert main() == 1
