"""Runner-local role resolution precedence, keyed by hub username (issue #95)."""

from __future__ import annotations

import pytest

from blizzard.auth_core import Role
from blizzard.runner.auth.roles import LocalRole
from blizzard.runner.config import RunnerConfig

pytestmark = pytest.mark.unit


def _config(**kwargs: object) -> RunnerConfig:
    from pathlib import Path

    return RunnerConfig(root=Path("."), db_url="sqlite://", **kwargs)  # type: ignore[arg-type]


def test_per_user_override_beats_the_hub_claim() -> None:
    config = _config(auth_users=(("alice", "admin"),))
    assert LocalRole(config, username="alice", hub_role="guest").role is Role.ADMIN


def test_hub_role_default_guest_caps_an_unmatched_hub_admin() -> None:
    config = _config(auth_hub_role_default="guest")
    assert LocalRole(config, username="bob", hub_role="admin").role is Role.GUEST


def test_mirror_reproduces_the_hub_role() -> None:
    config = _config(auth_hub_role_default="mirror")
    assert LocalRole(config, username="carol", hub_role="contributor").role is Role.CONTRIBUTOR


def test_mirror_reproduces_a_hub_pending_role() -> None:
    """A hub identity still `pending` (no role granted yet, issue #210) mirrors to the
    runner as `pending` too — the fixed-cap floor and the mirror default both accept
    every :class:`Role` member, `pending` included, with no special-casing needed."""
    config = _config(auth_hub_role_default="mirror")
    assert LocalRole(config, username="erin", hub_role="pending").role is Role.PENDING


def test_a_hub_guest_with_a_local_override_operates_the_runner() -> None:
    config = _config(auth_users=(("dave", "contributor"),))
    assert LocalRole(config, username="dave", hub_role="guest").role is Role.CONTRIBUTOR


def test_no_hub_identity_is_ever_denied() -> None:
    config = _config(auth_hub_role_default="guest")
    role = LocalRole(config, username="unknown-user", hub_role="guest").role
    assert role is Role.GUEST  # a concrete role, never a raised error


def test_auth_superuser_names_the_runners_own_sovereign() -> None:
    config = _config(auth_superuser="root-op", auth_hub_role_default="guest")
    assert LocalRole(config, username="root-op", hub_role="guest").role is Role.SUPERUSER


def test_auth_superuser_wins_over_a_conflicting_per_user_override() -> None:
    config = _config(auth_superuser="root-op", auth_users=(("root-op", "guest"),))
    assert LocalRole(config, username="root-op", hub_role="guest").role is Role.SUPERUSER
