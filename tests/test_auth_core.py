"""``blizzard/auth_core/`` — the dependency-free shared authz vocabulary (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.auth_core import ROLE_PERMISSIONS, Permission, Role, expand

pytestmark = pytest.mark.unit


def test_pending_holds_no_permissions() -> None:
    assert expand(Role.PENDING) == frozenset()


def test_guest_holds_fleet_view_and_nothing_else() -> None:
    all_permissions = {
        Permission.FLEET_VIEW,
        Permission.CHUNK_INGEST,
        Permission.CHUNK_CONTROL,
        Permission.QUESTION_ANSWER,
        Permission.GATE_RESOLVE,
        Permission.QUEUE_REORDER,
        Permission.RUNNER_PAUSE,
        Permission.RUNNER_RETIRE,
        Permission.RUNNER_ADD,
        Permission.GRAPH_EDIT,
        Permission.USER_MANAGE,
        Permission.TRANSCRIPT_READ,
        Permission.ANALYTICS_ADMIN,
        Permission.CONFIG_EDIT,
    }
    guest = expand(Role.GUEST)
    assert Permission.FLEET_VIEW in guest
    assert guest == {Permission.FLEET_VIEW}
    assert guest & (all_permissions - {Permission.FLEET_VIEW}) == frozenset()


def test_fleet_view_belongs_to_guest_and_above() -> None:
    for role in (Role.GUEST, Role.CONTRIBUTOR, Role.ADMIN, Role.SUPERUSER):
        assert Permission.FLEET_VIEW in expand(role)
    assert Permission.FLEET_VIEW not in expand(Role.PENDING)


def test_every_role_is_declared_in_the_map() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_roles_are_cumulative_superuser_admin_contributor_guest_pending() -> None:
    """The role order (superuser > admin > contributor > guest > pending) holds as a
    permission-bundle superset chain; ``superuser`` equals ``admin`` in #91 since its
    only extra authority is a per-action rule in #94, not a distinct permission bit."""
    pending = expand(Role.PENDING)
    guest = expand(Role.GUEST)
    contributor = expand(Role.CONTRIBUTOR)
    admin = expand(Role.ADMIN)
    superuser = expand(Role.SUPERUSER)
    assert pending <= guest <= contributor <= admin <= superuser
    assert pending < guest < contributor < admin
    assert admin == superuser


def test_user_manage_is_admin_and_above() -> None:
    """``user:manage`` is held by ``admin``+, since the admin page is gated on it; the
    "only ``superuser`` grants ``admin``" rule lives in user management (#94), not
    this permission's tier."""
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.USER_MANAGE in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.USER_MANAGE not in expand(role)


def test_runner_pause_and_graph_edit_are_admin_and_above() -> None:
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.RUNNER_PAUSE in expand(role)
        assert Permission.GRAPH_EDIT in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.RUNNER_PAUSE not in expand(role)
        assert Permission.GRAPH_EDIT not in expand(role)


def test_runner_add_is_admin_and_above() -> None:
    """``runner:add`` mints a runner credential, so a contributor never holds it."""
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.RUNNER_ADD in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.RUNNER_ADD not in expand(role)


def test_runner_retire_is_admin_and_above() -> None:
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.RUNNER_RETIRE in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.RUNNER_RETIRE not in expand(role)


def test_analytics_admin_is_admin_and_above() -> None:
    """``analytics:admin`` gates the forced re-derive route — a
    mutation, so above the read-only ``transcript:read``, not ``contributor``+."""
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.ANALYTICS_ADMIN in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.ANALYTICS_ADMIN not in expand(role)


def test_transcript_read_is_contributor_and_above() -> None:
    """``transcript:read`` is held by ``contributor``+ — not by
    ``guest``, which holds every other read: a transcript carries everything a worker
    saw, not just the fleet's state."""
    for role in (Role.CONTRIBUTOR, Role.ADMIN, Role.SUPERUSER):
        assert Permission.TRANSCRIPT_READ in expand(role)
    for role in (Role.PENDING, Role.GUEST):
        assert Permission.TRANSCRIPT_READ not in expand(role)


def test_operating_write_permissions_are_contributor_and_above() -> None:
    operating = {
        Permission.CHUNK_INGEST,
        Permission.CHUNK_CONTROL,
        Permission.QUESTION_ANSWER,
        Permission.GATE_RESOLVE,
        Permission.QUEUE_REORDER,
    }
    for role in (Role.CONTRIBUTOR, Role.ADMIN, Role.SUPERUSER):
        assert operating <= expand(role)
    assert operating.isdisjoint(expand(Role.PENDING))
    assert operating.isdisjoint(expand(Role.GUEST))


def test_expand_returns_a_frozenset() -> None:
    assert isinstance(expand(Role.ADMIN), frozenset)


def test_config_edit_is_admin_and_above() -> None:
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert Permission.CONFIG_EDIT in expand(role)
    for role in (Role.PENDING, Role.GUEST, Role.CONTRIBUTOR):
        assert Permission.CONFIG_EDIT not in expand(role)
