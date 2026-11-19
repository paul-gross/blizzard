"""``HostedRunnerService`` over fakes: an add validated before the hub is asked and admitted whole, and a retire asked
of the hub before its fact, a retired runner's retire asking and writing nothing."""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.secrets.sealing import SealedValue
from blizzard.foundation.settings_record.vocabulary import Entry
from blizzard.runner.hosting.model import (
    API,
    OPERATOR,
    HostedRoster,
    HostedRunner,
    HostedRunnerFact,
    HostedRunnerOp,
    HostingFact,
    RunnerAdmission,
)
from blizzard.runner.hosting.service import (
    HostedRunnerService,
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

    def roster(self) -> HostedRoster:
        return HostedRoster(active=self.active(), retired=self.retired())

    def facts(self, runner_id: str) -> tuple[HostedRunnerFact, ...]:
        return tuple(self.held.get(runner_id, ()))

    def host(self, runner_ids: Sequence[str], hosting: HostingFact) -> tuple[str, ...]:
        raise NotImplementedError

    def append_fact(self, runner_id: str, op: HostedRunnerOp, hosting: HostingFact) -> None:
        self.held[runner_id].append(HostedRunnerFact(runner_id, hosting.at, op, hosting.actor, hosting.door))

    def record_retired(self, runner_id: str, hosting: HostingFact) -> bool:
        raise NotImplementedError

    def drop(self, runner_id: str) -> bool:
        raise NotImplementedError


class _Admission:
    def __init__(self, admitted: list[tuple[RunnerAdmission, SealedValue]], hosted: _Hosted | None) -> None:
        self._admitted = admitted
        self._hosted = hosted

    def admit(self, admission: RunnerAdmission, token: SealedValue) -> None:
        self._admitted.append((admission, token))
        if self._hosted is not None:
            runner_id = f"rn_{len(self._admitted)}"
            self._hosted.held[runner_id] = [HostedRunnerFact(runner_id, _AT, HostedRunnerOp.ADDED, OPERATOR, "api")]
            _NAMED[runner_id] = admission.identity.runner_name

    def record_joined(self, identity: RunnerIdentity, hosting: HostingFact) -> None:
        raise NotImplementedError


class _Admissions:
    """Admits into ``hosted`` when given, so a later add sees the admitted runner's name taken."""

    def __init__(self, hosted: _Hosted | None = None) -> None:
        self.admitted: list[tuple[RunnerAdmission, SealedValue]] = []
        self._hosted = hosted

    def admission(self, runner_id: str) -> _Admission:
        return _Admission(self.admitted, self._hosted)


class _Settings:
    def __init__(self, problems: Mapping[str, str] | None = None) -> None:
        self._problems = problems or {}

    def problems(self, name: str, entries: Mapping[str, Entry]) -> Mapping[str, str]:
        return self._problems


_NAMED: dict[str, str] = {}


class _Names:
    def names_of(self, runner_ids: Sequence[str]) -> Mapping[str, str]:
        named = {"rn_day": "day-1", **_NAMED}
        return {runner_id: named.get(runner_id, runner_id) for runner_id in runner_ids}

    def declared_names(self) -> frozenset[str]:
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


def _held(service: HostedRunnerService, runner_id: str) -> HostedRunner:
    held = service.held(runner_id)
    assert held is not None
    return held


def test_a_new_runner_binds_a_project_naming_no_workspace_to_the_hosts_only_one() -> None:
    new = NewRunner.of("night-1", [("p1", None), ("p2", "ws-b")], {"agents": 2}, workspaces=["ws-a"])

    assert new.projects == ("p1", "p2")
    assert new.entries == {
        "agents": Entry(2),
        "projects": Entry([{"project": "p1", "workspace": "ws-a"}, {"project": "p2", "workspace": "ws-b"}]),
    }


def test_a_new_runner_on_a_host_of_several_workspaces_leaves_an_unnamed_one_unbound() -> None:
    new = NewRunner.of("night-1", [("p1", None)], {}, workspaces=["ws-a", "ws-b"])

    assert new.entries == {"projects": Entry([{"project": "p1"}])}


def test_two_adds_of_one_name_at_once_admit_only_one() -> None:
    entered, release = threading.Event(), threading.Event()

    class _SlowHub(_Hub):
        def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
            entered.set()
            release.wait(5)
            return super().add_runner(name, projects=projects)

    hosted = _Hosted()
    admissions = _Admissions(hosted)
    service = _service(hosted, _SlowHub(), admissions)
    refused: list[RunnerSettingsInvalid] = []

    def add() -> None:
        try:
            service.add(_new("dup"), actor="alice", door=API)
        except RunnerSettingsInvalid as exc:
            refused.append(exc)

    first = threading.Thread(target=add)
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=add)
    second.start()
    second.join(0.2)
    release.set()
    first.join(5)
    second.join(5)

    assert len(admissions.admitted) == 1
    assert len(refused) == 1
    _NAMED.clear()


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

    service = _service(hosted, hub)
    retired = service.retire(_held(service, "rn_day"), force=True, actor="alice", door=API)

    assert (retired.name, retired.released_chunk_ids, retired.already) == ("day-1", ("ch_1",), False)
    assert hub.calls == ["retire rn_day"]
    assert [(f.op, f.actor, f.door) for f in hosted.facts("rn_day")][-1] == (HostedRunnerOp.RETIRED, "alice", API)


def test_a_retired_runners_retire_asks_and_writes_nothing() -> None:
    hosted, hub = _Hosted(rn_day=[HostedRunnerOp.ADDED, HostedRunnerOp.RETIRED]), _Hub()

    service = _service(hosted, hub)
    retired = service.retire(_held(service, "rn_day"), force=False, actor="alice", door=API)

    assert retired.already
    assert hub.calls == []
    assert len(hosted.facts("rn_day")) == 2


def test_a_runner_the_store_does_not_hold_is_not_held() -> None:
    assert _service(_Hosted(), _Hub()).held("rn_nobody") is None


def test_the_hubs_refusal_of_a_retire_records_nothing() -> None:
    hosted = _Hosted(rn_day=[HostedRunnerOp.ADDED])
    refused = HostRefused(HostRefusalReason.NOT_FOUND, 404, "runner rn_day is not under this host")
    service = _service(hosted, _Hub(refused))
    with pytest.raises(HostRunnerHubRefused):
        service.retire(_held(service, "rn_day"), force=False, actor="alice", door=API)
    assert len(hosted.facts("rn_day")) == 1
