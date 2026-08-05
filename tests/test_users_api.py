"""``GET /api/users`` / ``POST /api/users/{user_id}/role`` — the admin page's user
listing and role-assignment API (issue #94).

Proves the route wires the role-change rules correctly: gating, 404/400/403 mapping,
the "takes effect on next request without re-login" AC, and the rendered ``UserView``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.auth_core import Role
from tests.support import build_hub, seed_session, seed_user

pytestmark = pytest.mark.component


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


# --- gating -----------------------------------------------------------------


def test_list_users_is_401_with_no_session(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.client.get("/api/users").status_code == 401


def test_list_users_is_403_below_user_manage(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)

    resp = hub.client.get("/api/users", headers=_cookie(token))
    assert resp.status_code == 403


def test_assign_role_is_403_below_user_manage(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, contributor)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))
    assert resp.status_code == 403


# --- listing -----------------------------------------------------------------


def test_list_users_renders_every_row(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN, email="ada@example.com", display_name="Ada")
    seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    resp = hub.client.get("/api/users", headers=_cookie(token))
    assert resp.status_code == 200
    body = resp.json()
    assert {u["username"] for u in body} == {"ada", "grace"}
    ada = next(u for u in body if u["username"] == "ada")
    assert ada["display_name"] == "Ada"
    assert ada["email"] == "ada@example.com"
    assert ada["role"] == "admin"
    assert ada["identities"] == []
    assert ada["created_at"]


# --- role assignment -----------------------------------------------------------


def test_admin_promotes_a_guest_to_contributor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "contributor"


def test_assigned_role_is_returned_by_a_fresh_list_users_read(tmp_path: Path) -> None:
    """Assign-then-read-back (issue #209): a follow-up ``GET /api/users`` — a
    distinct request from the mutation, mirroring a page reload — must reflect
    the new role, not just the assignment response's own rendered ``UserView``."""
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    assign = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))
    assert assign.status_code == 200

    listing = hub.client.get("/api/users", headers=_cookie(token))
    assert listing.status_code == 200
    grace = next(u for u in listing.json() if u["username"] == "grace")
    assert grace["role"] == "contributor"


def test_role_change_takes_effect_on_the_subjects_next_request_without_re_login(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    admin_token = seed_session(hub, admin)
    pending_token = seed_session(hub, pending)

    assert hub.client.get("/api/chunks", headers=_cookie(pending_token)).status_code == 403

    promote = hub.client.post(
        f"/api/users/{pending.user_id}/role", json={"role": "guest"}, headers=_cookie(admin_token)
    )
    assert promote.status_code == 200

    # Same session token, no re-login — the resolver reads `users.role` live.
    assert hub.client.get("/api/chunks", headers=_cookie(pending_token)).status_code == 200


def test_admin_granting_admin_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "admin"}, headers=_cookie(token))
    assert resp.status_code == 403


def test_superuser_granting_admin_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    superuser = seed_user(hub, username="root", role=Role.SUPERUSER)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, superuser)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "admin"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_self_role_change_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{admin.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))
    assert resp.status_code == 403


def test_superuser_is_not_assignable_through_the_api(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    superuser = seed_user(hub, username="root", role=Role.SUPERUSER)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, superuser)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "superuser"}, headers=_cookie(token))
    assert resp.status_code == 403


def test_admin_promotes_a_pending_user_to_guest(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{pending.user_id}/role", json={"role": "guest"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "guest"


def test_admin_promotes_a_pending_user_to_contributor(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{pending.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "contributor"


def test_admin_demotes_a_guest_to_pending(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "pending"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "pending"


def test_admin_granting_admin_from_pending_is_refused(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{pending.user_id}/role", json={"role": "admin"}, headers=_cookie(token))
    assert resp.status_code == 403


def test_superuser_granting_admin_from_pending_succeeds(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    superuser = seed_user(hub, username="root", role=Role.SUPERUSER)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, superuser)

    resp = hub.client.post(f"/api/users/{pending.user_id}/role", json={"role": "admin"}, headers=_cookie(token))
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"


def test_each_pending_role_change_emits_one_user_role_changed_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    pending = seed_user(hub, username="newcomer", role=Role.PENDING)
    token = seed_session(hub, admin)

    hub.client.post(f"/api/users/{pending.user_id}/role", json={"role": "guest"}, headers=_cookie(token))

    facts = hub.services.auth_facts.list_recent()
    assert len(facts) == 1
    assert facts[0].kind == "user_role_changed"
    assert facts[0].actor == "ada"
    assert facts[0].subject == "newcomer"
    assert facts[0].detail == "pending -> guest"


def test_assign_role_404s_for_an_unknown_user(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    token = seed_session(hub, admin)

    resp = hub.client.post("/api/users/usr_missing/role", json={"role": "contributor"}, headers=_cookie(token))
    assert resp.status_code == 404


def test_assign_role_400s_for_an_unknown_role_string(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    resp = hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "wizard"}, headers=_cookie(token))
    assert resp.status_code == 400


def test_each_role_change_emits_a_user_role_changed_fact(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, admin)

    hub.client.post(f"/api/users/{guest.user_id}/role", json={"role": "contributor"}, headers=_cookie(token))

    facts = hub.services.auth_facts.list_recent()
    assert len(facts) == 1
    assert facts[0].kind == "user_role_changed"
    assert facts[0].actor == "ada"
    assert facts[0].subject == "grace"
    assert facts[0].detail == "guest -> contributor"


# --- auth.mode = "none" ----------------------------------------------------------


def test_users_api_is_inert_under_none_mode(tmp_path: Path) -> None:
    """Under ``none`` the route still answers, but there is no store-backed user to list."""
    hub = build_hub(tmp_path)  # auth_mode defaults to "none"
    resp = hub.client.get("/api/users")
    assert resp.status_code == 200
    assert resp.json() == []
