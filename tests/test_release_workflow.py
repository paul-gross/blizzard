"""``.github/workflows/release.yml``'s ``release`` job — two static properties the
next tag cut can't rehearse locally (no buildx plugin on this machine, and a
regression here surfaces only as a broken release, never as a failing build):

1. The image build-push step runs **before** `gh release create` (decision 5 —
   a failed image build must never leave a published Release advertising an
   image that does not exist).
2. The job's `permissions:` block declares **both** `contents: write` (the
   existing `gh release create` step needs it) and `packages: write` (the GHCR
   push needs it) — a job-level `permissions:` block *replaces* the
   workflow-level one rather than merging with it, so declaring `packages:
   write` alone would silently strip `contents: write` and 403 every tagged
   release right after a successful image build.

No docker/GHCR credentials needed — this is a pure YAML parse, same docker-free
static-guard shape as ``tests/test_container_image.py`` / ``tests/test_systemd_units.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[1]
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
        "the image build-push step must run before `gh release create` (decision 5) — "
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
    strip the workflow-level `contents: write` the same job's `gh release create`
    step depends on, since a job-level block replaces rather than merges."""
    permissions = _release_job().get("permissions")
    assert permissions, (
        "the release job must declare its own job-level `permissions:` block "
        "(needed once it writes GHCR packages) — an absent block would inherit only "
        "the workflow-level contents:write and leave the GHCR push unauthorized"
    )
    assert permissions.get("contents") == "write"
    assert permissions.get("packages") == "write"


def test_version_tag_check_runs_before_anything_is_built() -> None:
    """The version/tag agreement check (issue #190) must fail fast, before the
    wheel or image build spends any CI time on a release that can't publish."""
    steps = _release_job()["steps"]
    check_idx = _step_index(steps, lambda s: "check-version-tag.sh" in str(s.get("run", "")))
    wheel_build_idx = _step_index(steps, lambda s: "build-wheel.sh" in str(s.get("run", "")))
    assert check_idx < wheel_build_idx


def test_release_notes_are_generated_not_auto_generated() -> None:
    """`gh release create` must consume the commit-type-grouped notes
    (scripts/release-notes.sh, tests/test_release_notes.py), not GitHub's own
    `--generate-notes`."""
    steps = _release_job()["steps"]
    notes_gen_idx = _step_index(steps, lambda s: "release-notes.sh" in str(s.get("run", "")))
    publish_idx = _step_index(steps, lambda s: "gh release create" in str(s.get("run", "")))
    assert notes_gen_idx < publish_idx
    publish_run = str(steps[publish_idx].get("run", ""))
    assert "--notes-file" in publish_run
    assert "--generate-notes" not in publish_run


def test_release_job_waits_for_the_full_suite_tiers() -> None:
    """A tag release must not publish ahead of the full suite (service tier,
    FULL crash sweep, e2e) — `gate` alone isn't sufficient for a release the
    way it is for the master dev channel (issue #200)."""
    assert "full-suite-tiers" in _release_job()["needs"]


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
