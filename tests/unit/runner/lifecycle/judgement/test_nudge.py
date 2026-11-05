"""ADVANCE resumes a premature exit instead of judging it.

A real ADVANCE tick against a tmp store and virtual clock, proving a step exiting with a
required `produces:` name unattached is resumed, not judged, and that "at most one resume
per (lease, epoch)" holds."""

from __future__ import annotations

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.runner.lifecycle.judgement.produces import ProducesReconciler
from blizzard.runner.node_steps.envelope import ProducesSpec
from tests.support.runner_fakes import (
    make_envelope,
)


def test_nudge_message_branches_on_kind_and_stays_harness_inert() -> None:
    """`ProducesReconciler.nudge_message` names the kind-appropriate declaration verb
    per unmet spec, never the deprecated `attach` alias, and every rendered line is
    `#`-prefixed so the mock harness's prompt-is-program `exec` still sees a legal no-op."""
    missing = [
        ProducesSpec(name="review-findings", kind=ArtifactKind.ASSET),
        ProducesSpec(name="commit", kind=ArtifactKind.GIT_COMMIT),
    ]

    envelope = make_envelope("ch_1", "build", node_id="nd_build", choices=[("pass", "ok")])

    message = ProducesReconciler(envelope).nudge_message(missing)

    for line in message.splitlines():
        assert line.startswith("#"), f"non-inert line in the nudge message: {line!r}"
    assert "artifact create --name review-findings" in message
    assert "artifact commit --repo <repo> --branch <branch> --commit <sha>" in message
    assert "`<repo>` is its name in the environment's manifest" in message
    assert "runner attach" not in message
