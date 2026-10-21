"""The runner-spawned-child environment allowlist (``bzh:worker-env-allowlist``).

The one builder every runner-launched child's env comes from: the **tool profile** for the runner's own tools, the
**worker profile** for worker, judge and resume spawns, which add the per-spawn identity. Never a full ``os.environ``
copy: anything not named here — foremost a daemon credential — is absent from every child by construction."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.roles import domain_model

# SAFETY: `ANTHROPIC_MODEL` and family must stay absent — here and in `[worker]
# env_passthrough` — they override the model a resumed session restores.
BASE_ALLOWLIST_VARS: tuple[str, ...] = ("PATH", "HOME", "USER", "LANG", "TERM", "TMPDIR")
# ``LC_*`` locale vars are a family, not a fixed set of names, so they are matched by
# prefix rather than enumerated in ``BASE_ALLOWLIST_VARS``.
LOCALE_PREFIX = "LC_"


@domain_model
@dataclass(frozen=True)
class ChildEnv:
    """A resolved child environment — the only form the launch seam's ``env`` takes, so no caller hands a child a
    raw mapping. Any per-launch variable beyond a profile is an explicit :meth:`adding`."""

    pairs: tuple[tuple[str, str], ...]

    @classmethod
    def operator_shell(cls, additions: Mapping[str, str]) -> ChildEnv:
        """The operator's own shell env layered with ``additions`` — only for a foreground child run in the
        operator's terminal, whose base env is the operator's shell by contract."""
        return cls(tuple({**os.environ, **additions}.items()))

    def adding(self, extra: Mapping[str, str]) -> ChildEnv:
        return ChildEnv(tuple({**dict(self.pairs), **extra}.items()))

    @property
    def variables(self) -> dict[str, str]:
        return dict(self.pairs)


@domain_model
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
    def variables(self) -> dict[str, str]:  # ast-grep-ignore: bzh:property-delegates
        names = set(BASE_ALLOWLIST_VARS) | set(self.passthrough)
        env = {name: os.environ[name] for name in names if name in os.environ}
        env.update((k, v) for k, v in os.environ.items() if k.startswith(LOCALE_PREFIX))
        if self.path_prepend:
            env["PATH"] = self._composed_path()
        return env

    def tool(self) -> ChildEnv:
        """The tool profile: the allowlist alone, never a lease identity, lease token or tracing exporter."""
        return ChildEnv(tuple(self.variables.items()))

    def worker(self, identity: Mapping[str, str]) -> ChildEnv:
        """The worker profile: the tool profile plus the per-spawn ``identity`` a worker reads to reach its runner."""
        return self.tool().adding(identity)

    def _composed_path(self) -> str:
        """Configured entries first, in listed order, each once; a daemon ``PATH`` element
        equal to a prepended entry is dropped; no daemon ``PATH`` yields the prepend alone."""
        prepend = list(dict.fromkeys(self.path_prepend))
        daemon_entries = os.environ.get("PATH", "").split(os.pathsep) if "PATH" in os.environ else []
        rest = [entry for entry in daemon_entries if entry not in prepend]
        return os.pathsep.join(prepend + rest)
