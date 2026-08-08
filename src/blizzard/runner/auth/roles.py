"""Runner-local role resolution, keyed by hub **username** (issue #95).

Runner roles live **only** in ``blizzard-runner.toml``. Precedence: ``auth.superuser``
wins outright, then a ``[auth.users]`` override, then ``hub_role_default``. **No
identity is ever denied** — every branch resolves to a concrete :class:`Role`, keyed on
``username`` only, never ``email``, which is mutable and may be null."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.auth_core import Role
from blizzard.runner.config import RunnerConfig

#: ``[auth].hub_role_default`` sentinel meaning "reproduce the hub's own claimed role"
#: rather than floor it to a fixed cap.
MIRROR = "mirror"


@dataclass(frozen=True)
class LocalRole:
    """A hub-federated ``username``/``hub_role`` pair, resolved against this runner's config.

    ``hub_role`` is the JWT's own coarse ``role`` claim (a :class:`Role` value) — held as
    ``str`` here since it arrives off the wire as one (``runner/auth/validate.py``)."""

    config: RunnerConfig
    username: str
    hub_role: str

    @property
    def role(self) -> Role:
        if self.config.auth_superuser is not None and self.username == self.config.auth_superuser:
            return Role.SUPERUSER
        overrides = dict(self.config.auth_users)
        if self.username in overrides:
            return Role(overrides[self.username])
        if self.config.auth_hub_role_default == MIRROR:
            return Role(self.hub_role)
        return Role(self.config.auth_hub_role_default)
