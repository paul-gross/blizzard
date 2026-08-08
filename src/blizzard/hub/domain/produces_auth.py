"""Produces-artifact authorization — the hub-side backstop on a node's ``produces:``
declaration (issue #113 phase 5).

The backstop against a submission carrying no explicit attachment and no covering git
commit for a declared name. Its coverage predicate is shared via
:class:`~blizzard.wire.completion.Coverage`, so the two models cannot drift."""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.hub.config import PRODUCES_ENFORCE
from blizzard.hub.domain.graph import Node
from blizzard.wire.completion import Coverage, SubmittedArtifact

_log = get_logger("blizzard.hub.produces_auth")


def check_produces(node: Node, submission_artifacts: list[SubmittedArtifact], *, mode: str) -> str | None:
    """Check that every ``node.produces`` spec is covered by the submission, per its
    declared kind (issue #143, D2).

    Returns a failure detail to reject with under ``enforce``, naming every uncovered spec,
    or ``None`` to proceed — under ``warn`` a gap is logged and proceeds."""
    if not node.produces:
        return None
    missing = [spec.name for spec in Coverage(submission_artifacts).unmet(node.produces)]
    if not missing:
        return None
    if mode == PRODUCES_ENFORCE:
        return (
            f"node `{node.name}` declares produces {missing} with no explicit "
            f"`blizzard runner attach` and no covering git commit"
        )
    _log.warning(
        "produces check failed: missing explicit attachment or covering commit",
        node=node.name,
        missing=missing,
    )
    return None
