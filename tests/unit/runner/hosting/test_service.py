"""``HostedRunnerService`` over fakes: an add validated before the hub is asked and admitted whole, and a retire asked
of the hub before its fact, a retired runner's retire asking and writing nothing."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.hosting.model import API, OPERATOR, HostedRunnerFact, HostedRunnerOp, HostingFact, RunnerAdmission
from blizzard.runner.hosting.service import (
    HostedRunnerService,
    HostedRunnerUnknown,
    HostRunnerHubRefused,
    NewRunner,
    RunnerSettingsInvalid,
)
from blizzard.runner.hub.client import HostRefusalReason, HostRefused, IssuedIdentity, RetiredHostRunner
from blizzard.runner.hub.identity import RunnerIdentity

_AT = datetime(2026, 11, 19, tzinfo=UTC)
_SEALED = SealedValue(key_id="k1", nonce=b"n", ciphertext=b"c")


class _Hosted:
    def __init__(self, **facts: list[HostedRunnerOp]) -> None:
        self.held = {
            runner_id: [HostedRunnerFact(runner_id, _AT, op, OPERATOR, "api") for op in ops]
            for runner_id, ops in facts.items()
        }

    def hosted_runner_ids(self) -> frozenset[str]:
        return frozenset(self.held)

    def active(self) -> tuple[str, ...]:
        return tuple(r for r, facts in self.held.items() if all(f.op is HostedRunnerOp.ADDED for f in facts))

    def retired(self) -> frozenset[str]:
        return frozenset(self.held) - set(self.active())

    def facts(self, runner_id: str) -> tuple[HostedRunnerFact, ...]:
        return tuple(self.held.get(runner_id, ()))

    def host(self, runner_ids: Sequence[str], hosting: HostingFact) -> tuple[str, ...]:
        raise NotImplementedError

    def append_fact(self, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
        self.held[runner_id].append(HostedRunnerFact(runner_id, hosting.at, op, hosting.actor, hosting.door))

    def drop(self, runner_id: str) -> bool:
        raise NotImplementedError


class _Admission:
    def __init__(self, admitted: list[tuple[RunnerAdmission, SealedValue]]) -> None:
        self._admitted = admitted

    def admit(self, admission: RunnerAdmission, token: SealedValue) -> None:
        self._admitted.append((admission, token))

    def record_joined(self, identity: RunnerIdentity, hosting: HostingFact) -> None:
        raise NotImplementedError


class _Admissions:
    def __init__(self) -> None:
        self.admitted: list[tuple[RunnerAdmission, SealedValue]] = []

    def admission(self, runner_id: str) -> _Admission:
        return _Admission(self.admitted)


class _Settings:
    def __init__(self, problems: Mapping[str, str] | None = None) -> None:
        self._problems = problems or {}

    def problems(self, name: str, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        return self._problems


class _Names:
    def name_of(self, runner_id: str) -> str:
        return {"rn_day": "day-1"}.get(runner_id, runner_id)

    def unhosted_names(self) -> frozenset[str]:
        return frozenset({"scaffold"})


class _Sealer:
    def seal_first(self, runner_id: str, token: str) -> SealedValue:
        return _SEALED


class _Hub:
    def __init__(self, refused: HostRefused | None = None) -> None:
        self.refused = refused
        self.calls: list[str] = []

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        self.calls.append(f"add {name}")
        return self.refused or IssuedIdentity(runner_id="rn_new", runner_name=name, token="tok")

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        self.calls.append(f"retire {runner_id}")
        return self.refused or RetiredHostRunner(runner_id=runner_id, released_chunk_ids=("ch_1",))


def _service(
    hosted: _Hosted, hub: _Hub, admissions: _Admissions | None = None, settings: _Settings | None = None
) -> HostedRunnerService:
    return HostedRunnerService(
        hosted=hosted,
        admissions=admissions or _Admissions(),
        settings=settings or _Settings(),
        names=_Names(),
        tokens=_Sealer(),
        hub=hub,
        clock=FixedClock(_AT),
    )


def _new(name: str = "night-1") -> NewRunner:
    return NewRunner(name=name, projects=("default",), entries={"agents": Entry(1)})


def test_an_add_mints_at_the_hub_then_admits_the_runner_whole() -> None:
    hub, admissions = _Hub(), _Admissions()

    added = _service(_Hosted(rn_day=[HostedRunnerOp.ADDED]), hub, admissions).add(_new(), actor="alice", door=API)

    assert (added.runner_id, added.name) == ("rn_new", "night-1")
    assert hub.calls == ["add night-1"]
    ((admission, token),) = admissions.admitted
    assert (admission.identity.runner_id, admission.actor, admission.door, admission.at) == (
        "rn_new",
        "alice",
        API,
        _AT,
    )
    assert admission.entries == {"agents": Entry(1), "name": Entry("night-1")}
    assert token == _SEALED


@pytest.mark.parametrize("name", ["day-1", "scaffold"])
def test_an_add_under_a_name_the_host_holds_is_refused_before_the_hub(name: str) -> None:
    hub = _Hub()
    with pytest.raises(RunnerSettingsInvalid) as refused:
        _service(_Hosted(rn_day=[HostedRunnerOp.ADDED]), hub).add(_new(name), actor="alice", door=API)
    assert "name" in refused.value.problems
    assert hub.calls == []


def test_a_retired_runners_name_is_free_again() -> None:
    hosted = _Hosted(rn_day=[HostedRunnerOp.ADDED, HostedRunnerOp.RETIRED])
    assert _service(hosted, _Hub()).add(_new("day-1"), actor="alice", door=API).name == "day-1"


def test_an_add_whose_settings_would_not_resolve_names_every_key_and_asks_nothing() -> None:
    hub, admissions = _Hub(), _Admissions()
    with pytest.raises(RunnerSettingsInvalid) as refused:
        _service(_Hosted(), hub, admissions, _Settings({"agents": "must be a positive integer"})).add(
            _new("day-1"), actor="alice", door=API
        )
    assert refused.value.problems == {"agents": "must be a positive integer"}
    assert (hub.calls, admissions.admitted) == ([], [])


def test_the_hubs_refusal_of_the_host_admits_nothing() -> None:
    admissions = _Admissions()
    refused = HostRefused(HostRefusalReason.CREDENTIAL_REFUSED, 401, "unknown host token")
    with pytest.raises(HostRunnerHubRefused):
        _service(_Hosted(), _Hub(refused), admissions).add(_new(), actor="alice", door=API)
    assert admissions.admitted == []


def test_a_retire_asks_the_hub_then_records_the_fact() -> None:
    hosted, hub = _Hosted(rn_day=[HostedRunnerOp.ADDED]), _Hub()

    retired = _service(hosted, hub).retire("rn_day", force=True, actor="alice", door=API)

    assert (retired.name, retired.released_chunk_ids, retired.already) == ("day-1", ("ch_1",), False)
    assert hub.calls == ["retire rn_day"]
    assert [(f.op, f.actor, f.door) for f in hosted.facts("rn_day")][-1] == (HostedRunnerOp.RETIRED, "alice", API)


def test_a_retired_runners_retire_asks_and_writes_nothing() -> None:
    hosted, hub = _Hosted(rn_day=[HostedRunnerOp.ADDED, HostedRunnerOp.RETIRED]), _Hub()

    retired = _service(hosted, hub).retire("rn_day", force=False, actor="alice", door=API)

    assert retired.already
    assert hub.calls == []
    assert len(hosted.facts("rn_day")) == 2


def test_a_retire_of_a_runner_the_store_does_not_hold_is_unknown() -> None:
    hub = _Hub()
    with pytest.raises(HostedRunnerUnknown):
        _service(_Hosted(), hub).retire("rn_nobody", force=False, actor="alice", door=API)
    assert hub.calls == []


def test_the_hubs_refusal_of_a_retire_records_nothing() -> None:
    hosted = _Hosted(rn_day=[HostedRunnerOp.ADDED])
    refused = HostRefused(HostRefusalReason.NOT_FOUND, 404, "runner rn_day is not under this host")
    with pytest.raises(HostRunnerHubRefused):
        _service(hosted, _Hub(refused)).retire("rn_day", force=False, actor="alice", door=API)
    assert len(hosted.facts("rn_day")) == 1
