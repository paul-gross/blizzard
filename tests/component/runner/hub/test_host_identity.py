"""The host's identity at its hub: the store's single ``host_identity`` row, written once and never edited, and the
process holder every consumer reads."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.runner.hub.host_identity import HostIdentity, HostIdentityAlreadyRecorded
from tests.runner_fakes import make_host_stores, make_store
from tests.support_host_identity import (
    _AT,  # pyright: ignore[reportPrivateUsage]
    _IDENTITY,  # pyright: ignore[reportPrivateUsage]
)


def test_a_fresh_store_holds_no_host_identity(tmp_path: Path) -> None:
    stores = make_host_stores(make_store(f"sqlite:///{tmp_path / 'r.db'}", runner_id=None))
    assert stores.host_identity.host_identity() is None


def test_the_first_identity_is_recorded_and_read_back(tmp_path: Path) -> None:
    stores = make_host_stores(make_store(f"sqlite:///{tmp_path / 'r.db'}", runner_id=None))
    stores.host_identity.record_host_identity(_IDENTITY)
    assert stores.host_identity.host_identity() == _IDENTITY


def test_recording_the_held_identity_again_writes_nothing(tmp_path: Path) -> None:
    stores = make_host_stores(make_store(f"sqlite:///{tmp_path / 'r.db'}", runner_id=None))
    stores.host_identity.record_host_identity(_IDENTITY)
    stores.host_identity.record_host_identity(HostIdentity("hs_1", "tn_1", datetime(2027, 1, 1, tzinfo=UTC)))
    assert stores.host_identity.host_identity() == _IDENTITY


@pytest.mark.parametrize("offered", [HostIdentity("hs_2", "tn_1", _AT), HostIdentity("hs_1", "tn_2", _AT)])
def test_another_host_or_tenant_is_refused_and_the_row_stands(tmp_path: Path, offered: HostIdentity) -> None:
    stores = make_host_stores(make_store(f"sqlite:///{tmp_path / 'r.db'}", runner_id=None))
    stores.host_identity.record_host_identity(_IDENTITY)
    with pytest.raises(HostIdentityAlreadyRecorded) as refused:
        stores.host_identity.record_host_identity(offered)
    assert (refused.value.held, refused.value.offered) == (_IDENTITY, offered)
    assert stores.host_identity.host_identity() == _IDENTITY
