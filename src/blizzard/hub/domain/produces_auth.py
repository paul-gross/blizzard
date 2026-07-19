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
:func:`~blizzard.wire.completion.satisfied_produces_names`, so the two models cannot
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
from blizzard.wire.completion import SubmittedArtifact, satisfied_produces_names

_log = get_logger("blizzard.hub.produces_auth")


def check_produces(node: Node, submission_artifacts: list[SubmittedArtifact], *, mode: str) -> str | None:
    """Check that every name in ``node.produces`` has an **explicit** artifact in the
    submission — one present with ``attached=True``, or a ``GIT_COMMIT`` artifact of
    that name (the runner's own coverage model, mirrored via
    :func:`~blizzard.wire.completion.satisfied_produces_names` — see that function's
    docstring for why this is the one shared home). A name that is missing entirely, or
    present only with ``attached=False`` and kind ``ASSET`` (the judgement-assessment
    fallback, phase 3), counts as lacking an explicit artifact: the fallback is a
    legitimate landing artifact (nothing here rejects it as content), but it is not proof
    the worker attached (or a git commit already covered) the thing the graph asked it to
    produce, which is exactly the gap this backstop watches for.

    Returns a failure detail to reject with under ``enforce``, naming every such name, or
    ``None`` to proceed (either every ``produces:`` name has an explicit attachment or a
    covering commit, or ``mode`` is ``warn`` and the gap was only logged).
    """
    if not node.produces:
        return None
    covered_names = satisfied_produces_names(submission_artifacts)
    missing = [name for name in node.produces if name not in covered_names]
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
