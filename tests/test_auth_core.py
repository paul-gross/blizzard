"""``blizzard/auth_core/`` — the dependency-free shared authz vocabulary (unit tier,
issue #91, decision D3).
"""

from __future__ import annotations

import pytest

from blizzard.auth_core import (
    CHUNK_CONTROL,
    CHUNK_INGEST,
    FLEET_VIEW,
    GATE_RESOLVE,
    GRAPH_EDIT,
    QUESTION_ANSWER,
    QUEUE_REORDER,
    ROLE_PERMISSIONS,
    RUNNER_PAUSE,
    USER_MANAGE,
    Role,
    expand,
)

pytestmark = pytest.mark.unit


def test_guest_holds_no_permissions() -> None:
    assert expand(Role.GUEST) == frozenset()


def test_fleet_view_belongs_to_contributor_and_above() -> None:
    for role in (Role.CONTRIBUTOR, Role.ADMIN, Role.SUPERUSER):
        assert FLEET_VIEW in expand(role)
    assert FLEET_VIEW not in expand(Role.GUEST)


def test_every_role_is_declared_in_the_map() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_roles_are_cumulative_superuser_admin_contributor_guest() -> None:
    """The role order (superuser > admin > contributor > guest) holds as a permission-
    bundle superset chain — reshaping one role's bundle can never remove a lower role's
    permission without this test naming it. The chain is strict up through ``admin``;
    ``superuser`` equals ``admin`` in #91 because its only extra authority — granting
    the ``admin`` role — is a per-action rule inside the #94 role-assignment route, not
    a distinct permission bit (see ``_SUPERUSER_PERMISSIONS``)."""
    guest = expand(Role.GUEST)
    contributor = expand(Role.CONTRIBUTOR)
    admin = expand(Role.ADMIN)
    superuser = expand(Role.SUPERUSER)
    assert guest <= contributor <= admin <= superuser
    assert guest < contributor < admin
    assert admin == superuser


def test_user_manage_is_admin_and_above() -> None:
    """The admin page is gated on ``user:manage`` and an ``admin`` uses it, so
    ``user:manage`` is held by ``admin``+ — the epic's "only ``superuser`` grants
    ``admin``" is a per-action rule inside user management (#94), not this permission's
    tier."""
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert USER_MANAGE in expand(role)
    for role in (Role.GUEST, Role.CONTRIBUTOR):
        assert USER_MANAGE not in expand(role)


def test_runner_pause_and_graph_edit_are_admin_and_above() -> None:
    for role in (Role.ADMIN, Role.SUPERUSER):
        assert RUNNER_PAUSE in expand(role)
        assert GRAPH_EDIT in expand(role)
    for role in (Role.GUEST, Role.CONTRIBUTOR):
        assert RUNNER_PAUSE not in expand(role)
        assert GRAPH_EDIT not in expand(role)


def test_operating_write_permissions_are_contributor_and_above() -> None:
    operating = {CHUNK_INGEST, CHUNK_CONTROL, QUESTION_ANSWER, GATE_RESOLVE, QUEUE_REORDER}
    for role in (Role.CONTRIBUTOR, Role.ADMIN, Role.SUPERUSER):
        assert operating <= expand(role)
    assert operating.isdisjoint(expand(Role.GUEST))


def test_expand_returns_a_frozenset() -> None:
    assert isinstance(expand(Role.ADMIN), frozenset)
