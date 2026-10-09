"""Hub administrators as facts over a migrated store: the newest fact per user is in force, any number of users hold
the role, each fact keeps who granted it, and the last holder is kept — even against a concurrent revocation."""

from __future__ import annotations

import shutil
import threading
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
import structlog

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.auth.errors import RepoErrorFactory
from blizzard.hub.auth.hub_admins import (
    SET_BY_BOOTSTRAP,
    SET_BY_MIGRATION,
    HubAdminFact,
    HubAdministrators,
    LastHubAdminKept,
    revocation_refusal,
)
from blizzard.hub.auth.internal.hub_admin_repository import HubAdminRepository
from blizzard.hub.auth.internal.identity_transaction import IdentityTransactions
from blizzard.hub.auth.models import User
from blizzard.hub.store import schema as s
from tests.support import hub_level_connections, hub_migration_prototype

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def engine(tmp_path: Path) -> sa.Engine:
    shutil.copyfile(hub_migration_prototype(), tmp_path / "hub.db")
    return create_engine_from_url(f"sqlite:///{tmp_path / 'hub.db'}")


def _user(user_id: str) -> User:
    return User(user_id, user_id, user_id, None, _T0)


def _held_open_after_reading_holders(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hold each revocation open just after it reads the holders, until the other revocation reads them too or half
    a second passes: two revocations that could both read before either writes are made to."""
    both_read = threading.Barrier(2, timeout=0.5)
    holders = HubAdminRepository.holders

    def held_open(self: HubAdminRepository) -> list[str]:
        read = holders(self)
        with suppress(threading.BrokenBarrierError):
            both_read.wait()
        return read

    monkeypatch.setattr(HubAdminRepository, "holders", held_open)


def _administrators(engine: sa.Engine, clock: FixedClock) -> HubAdministrators:
    transactions = IdentityTransactions(hub_level_connections(engine), RepoErrorFactory(structlog.get_logger("test")))
    return HubAdministrators(transactions=transactions, clock=clock)


def test_hub_admin_facts_carry_the_identity_contract_columns() -> None:
    assert [(c.name, c.nullable) for c in s.hub_admin_facts.columns] == [
        ("id", False),
        ("user_id", False),
        ("granted", False),
        ("set_at", False),
        ("set_by", False),
    ]
    assert s.hub_admin_facts.c.id.autoincrement is True


def test_the_newest_fact_per_user_is_in_force(engine: sa.Engine) -> None:
    repo = HubAdminRepository(hub_level_connections(engine))
    repo.record(HubAdminFact("usr_a", True, _T0, SET_BY_BOOTSTRAP))
    repo.record(HubAdminFact("usr_b", True, _T0 + timedelta(minutes=1), "usr_a"))
    repo.record(HubAdminFact("usr_b", False, _T0 + timedelta(minutes=2), "usr_a"))

    assert repo.is_hub_admin("usr_a") is True
    assert repo.is_hub_admin("usr_b") is False
    assert repo.is_hub_admin("usr_nobody") is False
    assert repo.holders() == ["usr_a"]

    repo.record(HubAdminFact("usr_b", True, _T0 + timedelta(minutes=3), "usr_a"))

    assert repo.is_hub_admin("usr_b") is True


def test_any_number_of_users_hold_the_role(engine: sa.Engine) -> None:
    administrators = _administrators(engine, FixedClock(_T0))
    for user_id in ("usr_c", "usr_a", "usr_b"):
        administrators.grant(_user(user_id), by=SET_BY_BOOTSTRAP)

    assert HubAdminRepository(hub_level_connections(engine)).holders() == ["usr_a", "usr_b", "usr_c"]


@pytest.mark.parametrize("by", ["usr_a", SET_BY_BOOTSTRAP, SET_BY_MIGRATION])
def test_a_grant_records_its_grantor_at_the_clocks_time(engine: sa.Engine, by: str) -> None:
    clock = FixedClock(_T0 + timedelta(hours=3))
    _administrators(engine, clock).grant(_user("usr_b"), by=by)

    with engine.connect() as conn:
        row = conn.execute(sa.select(s.hub_admin_facts)).one()
    assert (row.user_id, row.granted, row.set_at, row.set_by) == ("usr_b", True, clock.now(), by)


def test_the_last_hub_administrator_is_kept(engine: sa.Engine) -> None:
    administrators = _administrators(engine, FixedClock(_T0))
    administrators.grant(_user("usr_a"), by=SET_BY_BOOTSTRAP)
    administrators.grant(_user("usr_b"), by="usr_a")

    administrators.revoke(_user("usr_b"), by="usr_a")

    with pytest.raises(LastHubAdminKept, match="usr_a"):
        administrators.revoke(_user("usr_a"), by="usr_a")
    repo = HubAdminRepository(hub_level_connections(engine))
    assert repo.holders() == ["usr_a"]
    with engine.connect() as conn:
        assert conn.execute(sa.select(sa.func.count()).select_from(s.hub_admin_facts)).scalar_one() == 3


def test_two_concurrent_revocations_never_leave_the_hub_without_an_administrator(
    engine: sa.Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    administrators = _administrators(engine, FixedClock(_T0))
    administrators.grant(_user("usr_a"), by=SET_BY_BOOTSTRAP)
    administrators.grant(_user("usr_b"), by="usr_a")
    _held_open_after_reading_holders(monkeypatch)
    outcomes: dict[str, str] = {}

    def revoke(subject: str, by: str) -> None:
        try:
            administrators.revoke(_user(subject), by=by)
            outcomes[subject] = "revoked"
        except LastHubAdminKept:
            outcomes[subject] = "kept"

    threads = [threading.Thread(target=revoke, args=pair) for pair in (("usr_a", "usr_b"), ("usr_b", "usr_a"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(outcomes.values()) == ["kept", "revoked"]
    kept = next(user_id for user_id, outcome in outcomes.items() if outcome == "kept")
    assert HubAdminRepository(hub_level_connections(engine)).holders() == [kept]


@pytest.mark.parametrize(
    ("holders", "subject", "refused"),
    [
        (["usr_a"], "usr_a", True),
        (["usr_a", "usr_b"], "usr_a", False),
        (["usr_a"], "usr_b", False),
        ([], "usr_a", False),
    ],
)
def test_only_revoking_the_sole_holder_is_refused(holders: list[str], subject: str, refused: bool) -> None:
    assert (revocation_refusal(holders=holders, subject=subject) is not None) is refused
