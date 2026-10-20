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


def test_the_store_baseline_runs_only_when_a_pull_request_changes_a_store_revision() -> None:
    """The path check is a job, never a workflow-level ``paths:`` filter that would gate every other job."""
    assert "paths" not in _WORKFLOW[True]["pull_request"]
    jobs = _WORKFLOW["jobs"]
    job = jobs["store-baseline"]
    assert job["uses"] == "./.github/workflows/store-baseline.yml"
    assert job["needs"] == ["versions-changed"]
    assert job["if"] == "${{ needs.versions-changed.outputs.changed == 'true' }}"
    assert job["permissions"] == {"contents": "read"}
    for gated in ("gate", "upper-tiers"):
        assert "needs" not in jobs[gated]


def test_the_revision_check_diffs_both_versions_directories_between_base_and_head() -> None:
    job = _WORKFLOW["jobs"]["versions-changed"]
    checkout = next(s for s in job["steps"] if str(s.get("uses", "")).startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    diff = next(s for s in job["steps"] if s.get("id") == "diff")
    assert diff["env"] == {
        "BASE_SHA": "${{ github.event.pull_request.base.sha }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    assert 'git diff --quiet "$BASE_SHA...$HEAD_SHA"' in diff["run"]
    assert "src/blizzard/hub/store/migrations/versions" in diff["run"]
    assert "src/blizzard/runner/store/migrations/versions" in diff["run"]
    assert job["outputs"] == {"changed": "${{ steps.diff.outputs.changed }}"}
