"""The OpenCode 2 driver core: binding by service identity, settling a session tree by polling, recognizing a restart
by a changed identity rather than an outage, and interrupting a tree children first."""

from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from blizzard.runner.harness.opencode_v2.client import SessionCreate
from blizzard.runner.harness.opencode_v2.driver import (
    DriverCore,
    ServiceIdentity,
    ServiceIdentityError,
    ServiceNotRegisteredError,
    ServiceRestartedError,
    TurnTimeoutError,
    bind_service,
)
from blizzard.runner.harness.opencode_v2.registration import ServiceRegistration
from tests.opencode_v2_fakes import REGISTRATION, FakeService, FakeTransport

pytestmark = pytest.mark.unit


@dataclass
class Clock:
    now: float = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class Machine:
    """What is registered and what answers, both swappable mid-test as a restart swaps them."""

    registration: ServiceRegistration | None
    transport: FakeTransport

    def source(self) -> ServiceRegistration | None:
        return self.registration

    def restart(self) -> FakeService:
        assert self.transport.service is not None
        new = FakeService(pid=200, running=set(self.transport.service.running))
        self.transport.service = new
        self.registration = replace(REGISTRATION, id="svc-2", pid=200)
        return new


def _core(service: FakeService, clock: Clock | None = None) -> tuple[DriverCore, Machine, Clock]:
    machine = Machine(REGISTRATION, FakeTransport(service))
    clock = clock or Clock()
    core = DriverCore(
        machine.source,
        machine.transport,
        poll_seconds=1.0,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
        reach_bound_seconds=5.0,
    )
    return core, machine, clock


def test_binding_records_the_registered_identity() -> None:
    _, identity = bind_service(lambda: REGISTRATION, FakeTransport(FakeService()))
    assert identity == ServiceIdentity("svc-1", 100, "2.1.4")


def test_binding_refuses_when_nothing_is_registered() -> None:
    with pytest.raises(ServiceNotRegisteredError):
        bind_service(lambda: None, FakeTransport(FakeService()))


@pytest.mark.parametrize("answering", [FakeService(pid=999), FakeService(version="2.2.0")])
def test_binding_refuses_a_service_that_is_not_the_registered_one(answering: FakeService) -> None:
    with pytest.raises(ServiceIdentityError):
        bind_service(lambda: REGISTRATION, FakeTransport(answering))


def test_open_session_creates_then_sets_the_environment() -> None:
    service = FakeService()
    service.routes[("POST", "/api/session")] = lambda _request: (200, {"data": {"id": "ses_a"}})
    service.routes[("PUT", "/api/session/ses_a/environment")] = lambda _request: (200, None)
    core, machine, _ = _core(service)
    core.open_session(SessionCreate(id="ses_a", directory="/w", metadata={}), {"K": "v"})
    assert [(r.method, r.url.rsplit(":49374", 1)[1]) for r in machine.transport.requests[-2:]] == [
        ("POST", "/api/session"),
        ("PUT", "/api/session/ses_a/environment"),
    ]


def test_the_tree_lists_parents_before_children() -> None:
    core, _, _ = _core(FakeService(children={"ses_r": ["ses_c1", "ses_c2"], "ses_c1": ["ses_g"]}))
    assert core.tree("ses_r") == ("ses_r", "ses_c1", "ses_c2", "ses_g")


def test_a_tree_is_not_settled_while_a_descendant_runs_or_the_inbox_holds_a_wake() -> None:
    service = FakeService(children={"ses_r": ["ses_c"]}, running={"ses_c"})
    core, _, _ = _core(service)
    assert not core.tree_state("ses_r").settled()
    service.running.clear()
    service.inbox["ses_r"] = 1
    assert not core.tree_state("ses_r").settled()
    service.inbox.clear()
    assert core.tree_state("ses_r").settled()


def test_await_settled_polls_until_the_tree_settles() -> None:
    service = FakeService(running={"ses_r"})
    clock = Clock()
    core, _, _ = _core(service, clock)
    original_sleep = clock.sleep

    def settle_after_three(seconds: float) -> None:
        original_sleep(seconds)
        if clock.now >= 3:
            service.running.clear()

    core._sleep = settle_after_three  # pyright: ignore[reportPrivateUsage]  # drives the poll loop's time
    state = core.await_settled("ses_r", timeout_seconds=60)
    assert state.settled() and clock.now == 3


def test_await_settled_times_out() -> None:
    core, _, _ = _core(FakeService(running={"ses_r"}))
    with pytest.raises(TurnTimeoutError):
        core.await_settled("ses_r", timeout_seconds=3)


def test_a_restart_between_polls_is_recognized_by_identity_and_the_resumed_tree_is_interrupted() -> None:
    service = FakeService(running={"ses_r", "ses_c"}, children={"ses_r": ["ses_c"]})
    core, machine, _ = _core(service)
    new = machine.restart()
    new.children = {"ses_r": ["ses_c"]}
    with pytest.raises(ServiceRestartedError, match="restarted"):
        core.await_settled("ses_r", timeout_seconds=60)
    assert new.interrupted == ["ses_c", "ses_r"]
    assert service.interrupted == []


def test_a_brief_outage_of_the_same_service_does_not_fail_the_turn() -> None:
    service = FakeService(running={"ses_r"})
    core, machine, clock = _core(service)
    machine.transport.service = None
    core.check_service("ses_r")
    clock.now += 2
    machine.transport.service = service
    core.check_service("ses_r")
    service.running.clear()
    assert core.await_settled("ses_r", timeout_seconds=10).settled()


def test_await_settled_rides_out_a_brief_outage_of_the_same_service() -> None:
    service = FakeService(running={"ses_r"})
    core, machine, clock = _core(service)
    machine.transport.service = None
    polls: list[float] = []

    def back_after_two_polls(seconds: float) -> None:
        clock.now += seconds
        polls.append(clock.now)
        if len(polls) == 2:
            machine.transport.service = service
            service.running.clear()

    core._sleep = back_after_two_polls  # pyright: ignore[reportPrivateUsage]  # drives the poll loop's time
    assert core.await_settled("ses_r", timeout_seconds=60).settled()
    assert len(polls) == 2


def test_a_service_unreachable_past_the_bound_fails_the_turn() -> None:
    core, machine, clock = _core(FakeService(running={"ses_r"}))
    machine.transport.service = None
    core.check_service("ses_r")
    clock.now += 5
    with pytest.raises(ServiceRestartedError, match="stopped answering"):
        core.check_service("ses_r")


def test_interrupt_tree_stops_running_descendants_deepest_first_then_the_root() -> None:
    service = FakeService(
        children={"ses_r": ["ses_c1", "ses_c2"], "ses_c1": ["ses_g"]}, running={"ses_r", "ses_c1", "ses_g"}
    )
    core, _, _ = _core(service)
    assert core.interrupt_tree("ses_r") == ("ses_g", "ses_c1", "ses_r")
    assert service.interrupted == ["ses_g", "ses_c1", "ses_r"]
