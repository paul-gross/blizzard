"""The hosted-runner adapter and the runner admission: append-only facts, state from the newest fact, and an
admission written whole or not at all."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.composition import build_runner_admission
from blizzard.runner.hosting.model import CLI, OPERATOR, SEED, HostedRunnerOp, HostingFact, RunnerAdmission
from blizzard.runner.hosting.ports import IRunnerAdmission
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store import schema
from blizzard.runner.store.internal.host_hosted_runner_store import HostHostedRunnerStore
from blizzard.runner.store.internal.host_hub_token_store import HostHubTokenStore
from blizzard.runner.store.runner_guard import GUARD_ENVIRONMENT_VARIABLE, install_runner_guard
from tests.support.runner_fakes import make_store, runner_store_errors

_AT = datetime(2026, 11, 19, tzinfo=UTC)
_SEEDED = HostingFact(actor=SEED, door=SEED, at=_AT)
_TOKEN = SealedValue(key_id="k1", ciphertext=b"cipher", nonce=b"nonce")


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'host.db'}"


def _admission(url: str, runner_id: str) -> IRunnerAdmission:
    return build_runner_admission(
        make_store(url).engine, errors=runner_store_errors(), runner_id=runner_id, clock=FixedClock(_AT)
    )


def _added(runner_id: str, name: str) -> RunnerAdmission:
    return RunnerAdmission(
        identity=RunnerIdentity(runner_id, name, _AT),
        entries={"name": Entry(name), "projects": Entry(["web"])},
        actor="ada",
        door="api",
        at=_AT,
    )


def _count(url: str, table: sa.Table) -> int:
    with make_store(url).engine.connect() as conn:
        return int(conn.execute(sa.text(f"SELECT count(*) FROM {table.name}")).scalar_one())


def test_hosting_writes_a_row_and_an_added_fact_once_and_never_brings_a_retired_runner_back(tmp_path: Path) -> None:
    hosted = HostHostedRunnerStore(make_store(_url(tmp_path)).connections)
    assert hosted.host(["rn_a", "rn_b"], _SEEDED) == ("rn_a", "rn_b")
    assert hosted.host(["rn_a"], _SEEDED) == ()
    later = HostingFact(actor=OPERATOR, door=CLI, at=_AT + timedelta(hours=1))
    hosted.append_fact("rn_b", HostedRunnerOp.RETIRED, later)

    assert hosted.host(["rn_b"], later) == ()
    assert hosted.hosted_runner_ids() == {"rn_a", "rn_b"}
    assert hosted.active() == ("rn_a",)
    assert hosted.retired() == {"rn_b"}
    assert [(fact.op, fact.door, fact.recorded_at) for fact in hosted.facts("rn_b")] == [
        (HostedRunnerOp.ADDED, SEED, _AT),
        (HostedRunnerOp.RETIRED, CLI, later.at),
    ]
    assert hosted.facts("rn_never") == ()


def test_active_runners_keep_the_order_they_were_first_hosted(tmp_path: Path) -> None:
    hosted = HostHostedRunnerStore(make_store(_url(tmp_path)).connections)
    hosted.host(["rn_z"], _SEEDED)
    hosted.host(["rn_a"], HostingFact(actor=OPERATOR, door=CLI, at=_AT + timedelta(minutes=1)))
    assert hosted.active() == ("rn_z", "rn_a")


def test_an_admission_writes_the_runner_and_host_halves_together(tmp_path: Path) -> None:
    url = _url(tmp_path)
    _admission(url, "rn_new").admit(_added("rn_new", "night-1"), _TOKEN)

    hosted = HostHostedRunnerStore(make_store(url).connections)
    assert hosted.active() == ("rn_new",)
    assert [(fact.actor, fact.door) for fact in hosted.facts("rn_new")] == [("ada", "api")]
    with make_store(url).engine.connect() as conn:
        assert conn.execute(sa.text("SELECT runner_name FROM runner_identity")).scalar_one() == "night-1"
        assert conn.execute(sa.text("SELECT revision FROM runner_settings")).scalar_one() == 1
        assert set(conn.execute(sa.text("SELECT key FROM runner_setting_entries")).scalars()) == {"name", "projects"}
        assert conn.execute(sa.text("SELECT op FROM runner_setting_changes")).scalar_one() == "create"
        assert conn.execute(sa.text("SELECT revision FROM runner_hub_tokens")).scalar_one() == 1


def test_an_admission_that_fails_part_way_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    url = _url(tmp_path)

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise RuntimeError("the token write failed")

    monkeypatch.setattr(HostHubTokenStore, "seal_admitted_locked", refuse)
    with pytest.raises(RuntimeError):
        _admission(url, "rn_new").admit(_added("rn_new", "night-1"), _TOKEN)

    for table in (
        schema.runner_identity,
        schema.runner_settings,
        schema.runner_setting_entries,
        schema.runner_setting_changes,
        schema.hosted_runners,
        schema.hosted_runner_facts,
        schema.runner_hub_tokens,
    ):
        assert _count(url, table) == 0, table.name


def test_a_join_on_a_store_hosting_none_takes_up_the_runners_it_already_names(tmp_path: Path) -> None:
    url = _url(tmp_path)
    make_store(url, "rn_old").record_runner_identity(RunnerIdentity("rn_old", "old", _AT))

    joined = HostingFact(actor=OPERATOR, door=CLI, at=_AT)
    _admission(url, "rn_new").record_joined(RunnerIdentity("rn_new", "new", _AT), joined)
    _admission(url, "rn_new").record_joined(RunnerIdentity("rn_new", "new", _AT), joined)

    hosted = HostHostedRunnerStore(make_store(url).connections)
    assert set(hosted.active()) == {"rn_new", "rn_old"}
    assert [fact.door for fact in hosted.facts("rn_old")] == [SEED]
    assert [fact.door for fact in hosted.facts("rn_new")] == [CLI]


def test_the_admission_passes_the_runner_statement_guard(tmp_path: Path) -> None:
    assert install_runner_guard({GUARD_ENVIRONMENT_VARIABLE: "1"})
    url = _url(tmp_path)
    make_store(url, "rn_other").record_runner_identity(RunnerIdentity("rn_other", "other", _AT))
    _admission(url, "rn_new").admit(_added("rn_new", "night-1"), _TOKEN)
    assert HostHostedRunnerStore(make_store(url).connections).hosted_runner_ids() == {"rn_other", "rn_new"}
