"""``.github/workflows/pr.yml``'s triggers and its release-line provenance job."""

from __future__ import annotations

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_WORKFLOW = yaml.safe_load((repo_root() / ".github" / "workflows" / "pr.yml").read_text())


def test_pull_requests_into_master_and_release_lines_run() -> None:
    assert _WORKFLOW[True] == {"pull_request": {"branches": ["master", "release/**"]}}


def test_the_gate_and_upper_tiers_run_for_every_base() -> None:
    for name in ("gate", "upper-tiers"):
        assert "if" not in _WORKFLOW["jobs"][name]


def test_line_provenance_runs_only_on_a_release_base() -> None:
    job = _WORKFLOW["jobs"]["line-provenance"]
    assert job["if"] == "${{ startsWith(github.base_ref, 'release/') }}"


def test_line_provenance_checks_the_full_history_between_base_and_head() -> None:
    job = _WORKFLOW["jobs"]["line-provenance"]
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    run = next(s for s in job["steps"] if "run" in s)
    assert run["run"] == './scripts/check-line-provenance.sh "$BASE_SHA" "$HEAD_SHA"'
    assert run["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
