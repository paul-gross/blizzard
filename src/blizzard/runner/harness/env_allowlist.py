"""The runner-spawned-child environment allowlist (``bzh:worker-env-allowlist``).

The one builder every child process the runner launches into a leased environment
constructs its environment from. Never a full ``os.environ`` copy: everything not named
here — foremost a daemon credential — is absent from any such child by construction, so
untrusted harness output cannot leak one. One owner, so no two seams drift apart."""

from __future__ import annotations

import os
from collections.abc import Sequence

# SAFETY: `ANTHROPIC_MODEL` and family must stay absent — here and in `[worker]
# env_passthrough` — they override the model a resumed session restores (issue #144).
BASE_ALLOWLIST_VARS: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
# ``LC_*`` locale vars are a family, not a fixed set of names, so they are matched by
# prefix rather than enumerated in ``BASE_ALLOWLIST_VARS``.
LOCALE_PREFIX = "LC_"


def allowlisted_env(passthrough: Sequence[str]) -> dict[str, str]:
    """The child env built from the base allowlist + ``LC_*`` + the operator's passthrough.

    Never a full ``os.environ`` copy (``bzh:worker-env-allowlist``) — see the module
    docstring. The one function every runner-side subprocess env construction builds from.
    """
    names = set(BASE_ALLOWLIST_VARS) | set(passthrough)
    env = {name: os.environ[name] for name in names if name in os.environ}
    env.update((k, v) for k, v in os.environ.items() if k.startswith(LOCALE_PREFIX))
    return env
