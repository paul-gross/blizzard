"""The runner-spawned-child environment allowlist (``bzh:worker-env-allowlist``).

The one builder every child process the runner launches into a leased environment
constructs its environment from. Never a full ``os.environ`` copy: everything not named
here — foremost a daemon credential — is absent from any such child by construction, so
untrusted harness output cannot leak one. One owner, so no two seams drift apart."""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass

# SAFETY: `ANTHROPIC_MODEL` and family must stay absent — here and in `[worker]
# env_passthrough` — they override the model a resumed session restores (issue #144).
BASE_ALLOWLIST_VARS: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
# ``LC_*`` locale vars are a family, not a fixed set of names, so they are matched by
# prefix rather than enumerated in ``BASE_ALLOWLIST_VARS``.
LOCALE_PREFIX = "LC_"


@dataclass(frozen=True)
class AllowlistedEnv:
    """The child env built from the base allowlist + ``LC_*`` + the operator's passthrough,
    with ``path_prepend`` leading ``PATH`` (``[worker] path_prepend``).

    Never a full ``os.environ`` copy (``bzh:worker-env-allowlist``) — see the module docstring."""

    passthrough: tuple[str, ...]
    #: Absolute directories, already validated, led onto every child's ``PATH`` ahead of the daemon's own.
    path_prepend: tuple[str, ...] = ()

    @classmethod
    def of(cls, passthrough: Sequence[str], *, path_prepend: Sequence[str] = ()) -> AllowlistedEnv:
        return cls(tuple(passthrough), tuple(path_prepend))

    @property
    def variables(self) -> dict[str, str]:
        names = set(BASE_ALLOWLIST_VARS) | set(self.passthrough)
        env = {name: os.environ[name] for name in names if name in os.environ}
        env.update((k, v) for k, v in os.environ.items() if k.startswith(LOCALE_PREFIX))
        if self.path_prepend:
            env["PATH"] = self._composed_path()
        return env

    def _composed_path(self) -> str:
        """Configured entries first, in listed order, each once; a daemon ``PATH`` element
        equal to a prepended entry is dropped; no daemon ``PATH`` yields the prepend alone."""
        prepend = list(dict.fromkeys(self.path_prepend))
        daemon_entries = os.environ.get("PATH", "").split(os.pathsep) if "PATH" in os.environ else []
        rest = [entry for entry in daemon_entries if entry not in prepend]
        return os.pathsep.join(prepend + rest)
