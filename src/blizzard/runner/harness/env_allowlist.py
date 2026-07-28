"""The runner-spawned-child environment allowlist (``bzh:worker-env-allowlist``).

The one builder every child process the runner launches into a leased environment —
the worker spawn, the judgement/resume harness turns, and the check subprocess
(issue #114) — constructs its environment from. Never a full ``os.environ`` copy:
everything not named here — foremost a daemon credential like ``BZ_HUB_TOKEN`` or a
forge token — is absent from any such child by construction, so a still-untrusted
harness prompt, transcript, or check command cannot leak one.

Lives here (not inside the ``claude_code_adapter``) because more than one seam now
builds a child env from it — the harness adapter and the check-runner adapter — and a
single owner is what keeps the two from drifting (``bzh:one-owner``).
"""

from __future__ import annotations

import os
from collections.abc import Sequence

# The base allowlist: what a child process needs to locate/run its interpreter and
# behave predictably in a headless shell, determined empirically against the real
# ``claude`` harness on the dogfooding fleet. Deliberately conservative — an operator
# widens it via ``[worker] env_passthrough`` (``RunnerConfig.worker_env_passthrough``)
# rather than this list growing ad hoc.
# `ANTHROPIC_MODEL` and its family are deliberately ABSENT and must stay absent (issue
# #144): they override the model Claude Code restores for a resumed session, which is the
# stickiness the mint-only `--model` contract rests on. A deployment that forwards one
# runs every resuming pool member on the wrong model with every test tier still green —
# so this is a deployment requirement, not a preference. The same warning applies to
# widening the list through `[worker] env_passthrough`.
BASE_ALLOWLIST_VARS: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
# ``LC_*`` locale vars are a family, not a fixed set of names, so they are matched by
# prefix rather than enumerated in ``BASE_ALLOWLIST_VARS``.
LOCALE_PREFIX = "LC_"


def allowlisted_env(passthrough: Sequence[str]) -> dict[str, str]:
    """The child env built from the base allowlist + ``LC_*`` + the operator's passthrough.

    Never a full ``os.environ`` copy (``bzh:worker-env-allowlist``): everything not named
    here — foremost a daemon credential like ``BZ_HUB_TOKEN`` — is absent from a
    worker/judge/resume/check child by construction. The one function every runner-side
    subprocess env construction builds from.
    """
    names = set(BASE_ALLOWLIST_VARS) | set(passthrough)
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update((k, v) for k, v in os.environ.items() if k.startswith(LOCALE_PREFIX))
    return env
