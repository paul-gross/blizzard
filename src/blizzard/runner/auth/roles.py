"""Runner-local role resolution, keyed by hub **username** (issue #95).

Runner roles live **only** in ``blizzard-runner.toml`` — nothing here ever touches the
hub store or admin page. Resolution imports :class:`~blizzard.auth_core.Role` from the
shared, dependency-free ``blizzard/auth_core/`` package (decision D3) rather than
reforking a copy, so a role's permission bundle is defined exactly once for both
daemons.

Precedence (issue #95's own AC): ``auth.superuser`` (a hub username naming this
runner's own sovereign) wins outright; then a ``[auth.users]`` per-user override; then
``hub_role_default`` (``"mirror"`` reproduces the hub's own claimed role, or a fixed
cap floors every unmatched identity); **no hub identity is ever denied** — every branch
resolves to a concrete :class:`Role`, keyed on ``username`` only (never ``email``,
which is mutable and may be null).
"""

from __future__ import annotations

from blizzard.auth_core import Role
from blizzard.runner.config import RunnerConfig

#: ``[auth].hub_role_default`` sentinel meaning "reproduce the hub's own claimed role"
#: rather than floor it to a fixed cap.
MIRROR = "mirror"


def resolve_local_role(config: RunnerConfig, *, username: str, hub_role: str) -> Role:
    """The runner-local role a hub-federated ``username``/``hub_role`` pair resolves to.

    ``hub_role`` is the JWT's own coarse ``role`` claim (a :class:`Role` value) — passed
    as ``str`` here since it arrives off the wire as one (``runner/auth/validate.py``).
    """
    if config.auth_superuser is not None and username == config.auth_superuser:
        return Role.SUPERUSER
    overrides = dict(config.auth_users)
    if username in overrides:
        return Role(overrides[username])
    if config.auth_hub_role_default == MIRROR:
        return Role(hub_role)
    return Role(config.auth_hub_role_default)
