"""``.github/workflows/push.yml``'s ``dev-image`` job — the dogfood image
channel published for every proven `master` commit (issue #200). Static YAML
assertions only, the same docker-free shape as ``tests/test_release_workflow.py``:
no docker/GHCR credentials, no live build.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "push.yml"


def _jobs() -> dict:
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    return workflow["jobs"]


def _dev_image_job() -> dict:
    return _jobs()["dev-image"]


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matched among: {[s.get('name') or s.get('uses') for s in steps]}")


def test_push_workflow_exists() -> None:
    assert _WORKFLOW_PATH.is_file()


def test_dev_image_job_waits_for_gate_and_upper_tiers() -> None:
    """The dev channel publishes only a *proven* master commit — it must not
    race ahead of either the merge gate or the push workflow's upper
    verification tiers (service tier + bounded crash sweep)."""
    needs = _dev_image_job()["needs"]
    assert "gate" in needs
    assert "upper-tiers" in needs


def test_dev_image_job_needs_dev_build_for_the_shared_version_string() -> None:
    """dev-image consumes dev-build's computed version (0.<minor>.0.dev<run>)
    for its OCI version annotation rather than recomputing it inline — one
    source of truth for what "the dev version" means for a given commit."""
    assert "dev-build" in _dev_image_job()["needs"]


def test_dev_build_job_exposes_its_version_as_an_output() -> None:
    dev_build = _jobs()["dev-build"]
    assert dev_build.get("outputs", {}).get("version")


def test_dev_image_job_declares_packages_write() -> None:
    permissions = _dev_image_job().get("permissions")
    assert permissions, "dev-image must declare its own job-level permissions block to push to GHCR"
    assert permissions.get("packages") == "write"


def test_derived_tags_include_edge_and_a_sha_tag_referencing_github_sha() -> None:
    steps = _dev_image_job()["steps"]
    derive = steps[_step_index(steps, lambda s: s.get("id") == "image_meta")]
    run = str(derive.get("run", ""))
    assert "ghcr.io/paul-gross/blizzard-hub:edge" in run
    assert "sha-${GITHUB_SHA}" in run or "sha-$GITHUB_SHA" in run


def test_build_push_step_consumes_the_derived_tags_and_targets_both_platforms() -> None:
    steps = _dev_image_job()["steps"]
    build_push = steps[_step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))]
    with_ = build_push.get("with", {})
    assert with_.get("push") is True
    tags_input = str(with_.get("tags", ""))
    assert "image_meta" in tags_input, "the build-push step must consume the derived tag list, not a literal"
    platforms = str(with_.get("platforms", ""))
    assert "linux/amd64" in platforms
    assert "linux/arm64" in platforms
