"""The adapter-drift canary (issue #54): ``blizzard runner selftest``.

Per-coding-harness mechanics — spawn with a pre-assigned session id and exit-is-done
detection, verdict elicitation, automated resume, resume-command composition — are
external CLI surface that drifts with every harness release. A selftest run
exercises all five against a single throwaway scratch git repo, minted and torn down
by the :mod:`.scratch_git` seam — no chunk, lease, environment binding, or hub call
is ever on this path. :mod:`.checks` is the deterministic orchestration
(``bzh:deterministic-shell``) over the harness and scratch-git seams
(``bzh:pluggable-seams``); :mod:`.service` is the in-memory job resource a selftest
run lives as, minted by ``POST /api/selftests`` and read back by
``GET /api/selftests/{id}``.
"""

from __future__ import annotations
