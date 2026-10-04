"""Runner-local role resolution, keyed by hub **username**.

Runner roles live **only** in ``blizzard-runner.toml``. Precedence: ``auth.superuser``
wins outright, then a ``[auth.users]`` override, then ``hub_role_default``. **No
identity is ever denied** — every branch resolves to a concrete :class:`Role`, keyed on
``username`` only, never ``email``, which is mutable and may be null."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.auth_core import Role
from blizzard.foundation.roles import domain_model

#: ``[auth].hub_role_default`` sentinel meaning "reproduce the hub's own claimed role"
#: rather than floor it to a fixed cap.
MIRROR = "mirror"


@domain_model
@dataclass(frozen=True)
class RolePolicy:
    """The runner's ``[auth]`` role precedence: the sovereign ``superuser``, the per-username
    ``users`` overrides, and the ``hub_role_default`` an unmatched identity falls back to."""

    superuser: str | None
    users: tuple[tuple[str, str], ...]
    hub_role_default: str


@domain_model
@dataclass(frozen=True)
class LocalRole:
    """A hub-federated ``username``/``hub_role`` pair, resolved against this runner's role policy.

    ``hub_role`` is the JWT's own coarse ``role`` claim (a :class:`Role` value) — held as
    ``str`` here since it arrives off the wire as one (``runner/auth/validate.py``)."""

    policy: RolePolicy
    username: str
    hub_role: str

    @property
    def role(self) -> Role:  # ast-grep-ignore: bzh:property-delegates
        if self.policy.superuser is not None and self.username == self.policy.superuser:
            return Role.SUPERUSER
        overrides = dict(self.policy.users)
        if self.username in overrides:
            return Role(overrides[self.username])
        if self.policy.hub_role_default == MIRROR:
            return Role(self.hub_role)
        return Role(self.policy.hub_role_default)
