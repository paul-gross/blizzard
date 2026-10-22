"""``FederationClient`` — what a hub-IdP sign-in needs of the runner or host it names (unit tier).

Pins each refusal and their order; the route's mapping to the one identical 400 and the retired 403 is pinned in
``tests/test_idp_authorize.py``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.auth_core import Role
from blizzard.hub.domain.runners.federation import FederationClient, UnknownFederationClient
from blizzard.hub.domain.runners.host import Host, HostRetired
from blizzard.hub.domain.runners.registration import RunnerRegistration, RunnerRetired
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_TENANT = TenantId("ten_one")
_REDIRECT = "http://box/callback"
_OTHER = "http://elsewhere/callback"

_RUNNER = RunnerRegistration(runner_id="rn_a", name="a", added_at=_T0, hub_paused=False, redirect_uris=(_REDIRECT,))
_HOST = Host(host_id="hs_a", name="box", added_at=_T0, token_hash="hash", redirect_uris=(_REDIRECT,))
_RETIRED_RUNNER = replace(_RUNNER, retired=True, retired_at=_T0, retired_by="op")
_RETIRED_HOST = replace(_HOST, token_hash=None, retired=True, retired_at=_T0, retired_by="op")

_RECORDS = [_RUNNER, _HOST]
_RETIRED = [(_RETIRED_RUNNER, RunnerRetired), (_RETIRED_HOST, HostRetired)]


@pytest.mark.parametrize("record", _RECORDS)
def test_a_member_at_a_registered_redirect_is_federated_with_their_role(record: RunnerRegistration | Host) -> None:
    client = FederationClient(_TENANT, record)
    client.admit(_REDIRECT)
    assert client.federate(_REDIRECT, role=Role.CONTRIBUTOR) is Role.CONTRIBUTOR


@pytest.mark.parametrize("record", _RECORDS)
def test_an_unregistered_redirect_is_an_unknown_client_before_and_after_authentication(
    record: RunnerRegistration | Host,
) -> None:
    client = FederationClient(_TENANT, record)
    with pytest.raises(UnknownFederationClient):
        client.admit(_OTHER)
    with pytest.raises(UnknownFederationClient):
        client.federate(_OTHER, role=Role.CONTRIBUTOR)


@pytest.mark.parametrize("record", _RECORDS)
def test_no_membership_is_an_unknown_client(record: RunnerRegistration | Host) -> None:
    with pytest.raises(UnknownFederationClient):
        FederationClient(_TENANT, record).federate(_REDIRECT, role=None)


@pytest.mark.parametrize(("record", "retired"), _RETIRED)
def test_a_retired_client_is_told_apart_only_by_a_member_at_a_registered_redirect(
    record: RunnerRegistration | Host, retired: type[Exception]
) -> None:
    client = FederationClient(_TENANT, record)
    with pytest.raises(retired):
        client.federate(_REDIRECT, role=Role.CONTRIBUTOR)
    with pytest.raises(UnknownFederationClient):
        client.federate(_OTHER, role=Role.CONTRIBUTOR)
    with pytest.raises(UnknownFederationClient):
        client.federate(_REDIRECT, role=None)
