"""``.github/workflows/release.yml`` — release ordering, permissions, and
version stamping. Parse YAML and run the tag derivation shell step without
Docker/GHCR credentials.
"""

from __future__ import annotations

import os
import subprocess

import pytest
import yaml

from tests.repo_files import repo_root

pytestmark = pytest.mark.unit

_REPO_ROOT = repo_root()
_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "release.yml"


def _release_job() -> dict:
    # YAML parses the bare `on:` key as the boolean `True` under PyYAML's default
    # (YAML 1.1) resolver — irrelevant here since only `jobs` is read.
    workflow = yaml.safe_load(_WORKFLOW_PATH.read_text())
    return workflow["jobs"]["release"]


def _step_index(steps: list[dict], predicate) -> int:
    for i, step in enumerate(steps):
        if predicate(step):
            return i
    raise AssertionError(f"no step matched among: {[s.get('name') or s.get('uses') for s in steps]}")


def test_release_workflow_exists() -> None:
    assert _WORKFLOW_PATH.is_file()


def test_image_build_push_step_precedes_the_release_publish_step() -> None:
    steps = _release_job()["steps"]
    build_push_idx = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    publish_idx = _step_index(steps, lambda s: "gh release create" in str(s.get("run", "")))
    assert build_push_idx < publish_idx, (
        "the image build-push step must run before `gh release create` — "
        "a failed image build must never leave a published Release advertising an "
        "image that does not exist"
    )


def test_image_build_push_step_pushes_and_targets_both_platforms() -> None:
    steps = _release_job()["steps"]
    build_push = steps[_step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))]
    with_ = build_push.get("with", {})
    assert with_.get("push") is True
    platforms = str(with_.get("platforms", ""))
    assert "linux/amd64" in platforms
    assert "linux/arm64" in platforms


def test_job_permissions_declare_both_contents_and_packages_write() -> None:
    """The regression this pins: adding `packages: write` alone would silently
    strip the `contents: write` the same job's `gh release create`
    step depends on, since a job-level block replaces rather than merges."""
    permissions = _release_job().get("permissions")
    assert permissions, (
        "the release job must declare its own job-level `permissions:` block "
        "(it writes GHCR packages and the Release) — the workflow level grants only read"
    )
    assert permissions.get("contents") == "write"
    assert permissions.get("packages") == "write"


def test_version_tag_check_runs_before_anything_is_built() -> None:
    """The version/tag agreement check must fail fast, before the
    wheel or image build spends any CI time on a release that can't publish."""
    steps = _release_job()["steps"]
    check_idx = _step_index(steps, lambda s: "check-version-tag.sh" in str(s.get("run", "")))
    wheel_build_idx = _step_index(steps, lambda s: "build-wheel.sh" in str(s.get("run", "")))
    assert check_idx < wheel_build_idx


@pytest.mark.parametrize("tag", ["v0.2.0-rc.1", "v0.2.0"])
def test_release_wheel_stamps_the_full_tag_version(tmp_path, tag: str) -> None:
    steps = _release_job()["steps"]
    derive = steps[_step_index(steps, lambda s: s.get("id") == "ver")]
    wheel = steps[_step_index(steps, lambda s: str(s.get("run", "")).strip() == "./scripts/build-wheel.sh")]
    assert wheel["env"]["BLIZZARD_VERSION"] == "${{ steps.ver.outputs.version }}"
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-e", "-c", derive["run"]],
        env={**os.environ, "GITHUB_REF_NAME": tag, "GITHUB_OUTPUT": str(output)},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert f"version={tag[1:]}" in output.read_text().splitlines()


def test_release_notes_are_generated_not_auto_generated() -> None:
    """The published notes come from the tag-tree renderer, including its flag summaries."""
    steps = _release_job()["steps"]
    notes_gen_idx = _step_index(steps, lambda s: "blizzard-change-notes render" in str(s.get("run", "")))
    publish_idx = _step_index(steps, lambda s: "gh release create" in str(s.get("run", "")))
    assert notes_gen_idx < publish_idx
    assert 'render "${GITHUB_REF_NAME}" > release-notes.md' in steps[notes_gen_idx]["run"]
    publish_run = str(steps[publish_idx].get("run", ""))
    assert "--notes-file release-notes.md" in publish_run
    assert "--generate-notes" not in publish_run


def test_release_job_waits_for_each_full_suite_tier() -> None:
    """A tag release must not publish ahead of the full suite's three parallel
    jobs (service tier, FULL crash sweep, e2e) — `gate` alone isn't sufficient
    for a release the way it is for the master dev channel."""
    needs = _release_job()["needs"]
    for job in ("service-tier", "crash-sweep", "e2e"):
        assert job in needs


def test_tags_are_derived_from_the_image_tags_script_not_hardcoded() -> None:
    """The semver fan-out logic lives in scripts/image-tags.sh (unit-tested in
    tests/test_image_tags.py) — the workflow step must consume its output, not
    re-implement the fan-out inline."""
    steps = _release_job()["steps"]
    derive_idx = _step_index(steps, lambda s: "image-tags.sh" in str(s.get("run", "")))
    build_push_idx = _step_index(steps, lambda s: "build-push-action" in str(s.get("uses", "")))
    assert derive_idx < build_push_idx
    build_push = steps[build_push_idx]
    tags_input = str(build_push.get("with", {}).get("tags", ""))
    assert "image_meta" in tags_input, "the build-push step must consume the derived tag list, not a literal"


def _workflow() -> dict:
    return yaml.safe_load(_WORKFLOW_PATH.read_text())


def test_workflow_level_permissions_are_read_only() -> None:
    """The test jobs inherit the workflow level, so it must grant no write scope."""
    assert _workflow()["permissions"] == {"contents": "read"}


def test_only_the_release_job_holds_a_write_scope() -> None:
    for name, job in _workflow()["jobs"].items():
        if name == "release":
            continue
        granted = job.get("permissions") or {}
        assert "write" not in (granted.values() if isinstance(granted, dict) else [granted]), (
            f"job {name!r} holds a write scope; only `release` publishes"
        )
