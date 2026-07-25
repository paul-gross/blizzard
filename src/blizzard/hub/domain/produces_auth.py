"""Produces-artifact authorization — the hub-side backstop on a node's ``produces:``
declaration (issue #113 phase 5).

Layered on top of two runner-side mechanisms: completion assembly
(``runner/loop/steps.py``'s ``_collect_asset_artifacts``, phase 3) prefers an explicit
``blizzard runner attach`` for each ``produces:`` name and falls back to the judgement
assessment when none was attached (``SubmittedArtifact.attached=False``); the runner's
own nudge-once (phase 4) resumes the worker a single time to give it a chance to attach
before submitting. This check is the **hub's** backstop against a submission that still
carries no explicit attachment, and no covering git commit, for one or more declared
names — a worker that ignored the nudge, or a graph the nudge never reached. It shares
its coverage predicate with the runner's own nudge check via
:func:`~blizzard.wire.completion.produces_coverage`, so the two models cannot
drift apart.

The check is a plain function, not a service — it takes already-loaded values
(``bzh:domain-takes-objects``): the caller resolves the ``Node`` from the pinned graph and
the submission's own artifacts, so this stays a pure function callable from
:mod:`~blizzard.hub.domain.apply` alone, mirroring
:func:`~blizzard.hub.domain.route_auth.check_route_token`'s shape (and its
``produces_mode`` rollout brake, ``hub/config.py``).
"""

from __future__ import annotations

from blizzard.foundation.logging import get_logger
from blizzard.hub.config import PRODUCES_ENFORCE
from blizzard.hub.domain.graph import Node
from blizzard.wire.completion import SubmittedArtifact, produces_coverage

_log = get_logger("blizzard.hub.produces_auth")


def check_produces(node: Node, submission_artifacts: list[SubmittedArtifact], *, mode: str) -> str | None:
    """Check that every ``node.produces`` spec is covered, per its declared kind, by the
    submission (issue #143, D2) — evaluated by
    :func:`~blizzard.wire.completion.produces_coverage`, the one shared predicate the
    runner's own nudge check also calls, so the two coverage models cannot drift apart.
    An ``asset`` spec needs an explicit ``attached=True`` artifact (or a ``GIT_COMMIT``
    artifact) of its own name; a ``git_commit`` spec needs **any** ``GIT_COMMIT``-kind
    artifact present — a kind match, not a name match, since a declared git-commit is
    named per-repo, never the produces name itself.

    The hub holds no worktree (``bzh:git-write-in-worker-seam``), so a ``git_commit`` spec is
    checked by presence-by-kind only here — forge verification against the declared
    branch/commit is the runner's job (a later phase), not this backstop's.

    Returns a failure detail to reject with under ``enforce``, naming every uncovered
    spec, or ``None`` to proceed (every ``produces:`` spec is covered, or ``mode`` is
    ``warn`` and the gap was only logged).
    """
    if not node.produces:
        return None
    missing = [spec.name for spec in produces_coverage(node.produces, submission_artifacts)]
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
