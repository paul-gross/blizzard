"""Giving a gate-parked chunk back: whether a runner offers, the offer letting go only on the hub's acceptance, the
HTTP client's reading of each answer, and the three call sites — a held chunk's next node, an adopted claim, and a
usage-limited park."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageLimit, UsageSample
from blizzard.runner.hub.client import HubClientError
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from blizzard.runner.leases.model import NewLease
from blizzard.runner.lifecycle.claim import InterruptedClaims
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.held_chunk import HeldChunk, offer_between_steps
from blizzard.runner.loop.context import LoopConfig, LoopContext, ResolvedSubscription
from blizzard.runner.loop.steps import Advance
from blizzard.runner.node_steps.chunk_state import ChunkPause, ChunkState
from blizzard.runner.throttle.pause import USAGE_LIMIT_PARK_CAUSE, PausePark, RunnerBrakes
from blizzard.runner.throttle.release import (
    Release,
    ReleaseReason,
    ReleaseRefusals,
    releases_between_steps,
    releases_on_decline,
    releases_usage_limited,
)
from blizzard.runner.throttle.settings import HoldOrRelease, ThrottleSettings
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    ReleaseCall,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_store,
    serving,
    usage_limit_mark,
)

_NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
_SLUG = "team-plan"
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100", pgid=100)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_ON_DECLINE = ThrottleSettings(on_decline=HoldOrRelease.RELEASE)
_ON_USAGE_LIMIT = ThrottleSettings(on_usage_limit=HoldOrRelease.RELEASE)
_PARK = PausePark(
    lease_id="lease_1", chunk_id="ch_1", parked_at=_NOW, interrupted_elicitation_id=None, cause=USAGE_LIMIT_PARK_CAUSE
)


# --- whether a runner offers, by value -------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "declined", "offers"),
    [
        (None, True, False),
        (ThrottleSettings(), True, False),
        (_ON_USAGE_LIMIT, True, False),
        (_ON_DECLINE, False, False),
        (_ON_DECLINE, True, True),
    ],
)
def test_a_step_is_offered_back_only_when_declined_on_a_runner_set_to_release(
    settings: ThrottleSettings | None, declined: bool, offers: bool
) -> None:
    assert releases_between_steps(settings, declined=declined) is offers
    assert releases_on_decline(settings) is (settings is _ON_DECLINE)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("settings", "park", "operator_holds", "declined", "offers"),
    [
        (_ON_USAGE_LIMIT, _PARK, False, True, True),
        (_ON_DECLINE, _PARK, False, True, False),
        (None, _PARK, False, True, False),
        (_ON_USAGE_LIMIT, dataclasses.replace(_PARK, cause=None), False, True, False),
        (_ON_USAGE_LIMIT, _PARK, True, True, False),
        (_ON_USAGE_LIMIT, _PARK, False, False, False),
    ],
)
def test_a_park_is_offered_back_only_when_a_usage_limit_wrote_it_and_its_floor_still_declines(
    settings: ThrottleSettings | None, park: PausePark, operator_holds: bool, declined: bool, offers: bool
) -> None:
    assert releases_usage_limited(settings, park, operator_holds=operator_holds, declined=declined) is offers


# --- the offer -------------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _ReleaseCtx:
    hub: FakeHub | HttpHubClient
    release_refusals: ReleaseRefusals = dataclasses.field(default_factory=ReleaseRefusals)


def _offer(
    hub: FakeHub | HttpHubClient, let_go: Callable[[], None], *, ctx: _ReleaseCtx | None = None, epoch: int = 3
) -> bool:
    return Release(ctx or _ReleaseCtx(hub)).offer(
        "ch_1", epoch=epoch, route_token="rtok", reason=ReleaseReason.THROTTLED, then=let_go
    )


@pytest.mark.unit
def test_an_accepted_offer_lets_go_once_and_sends_the_reason() -> None:
    hub, let_go = FakeHub(), _Counter()

    assert _offer(hub, let_go) is True

    assert let_go.calls == 1
    assert hub.releases == [ReleaseCall("ch_1", 3, "rtok", "throttled")]


@pytest.mark.unit
def test_a_refused_offer_holds() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.release_refused.add("ch_1")

    assert _offer(hub, let_go) is False
    assert let_go.calls == 0


@pytest.mark.unit
def test_a_refused_offer_is_not_repeated_at_the_same_epoch_but_is_at_the_next() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.release_refused.add("ch_1")
    ctx = _ReleaseCtx(hub)

    assert _offer(hub, let_go, ctx=ctx) is False
    assert _offer(hub, let_go, ctx=ctx) is False
    assert len(hub.releases) == 1

    hub.release_refused.clear()
    assert _offer(hub, let_go, ctx=ctx, epoch=4) is True
    assert len(hub.releases) == 2


@pytest.mark.unit
def test_an_unreachable_hub_holds() -> None:
    hub, let_go = FakeHub(), _Counter()
    hub.down = True

    assert _offer(hub, let_go) is False
    assert let_go.calls == 0


class _Counter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


# --- the HTTP client -------------------------------------------------------------------------------------------------


def _http(status: int, seen: list[httpx2.Request] | None = None) -> HttpHubClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if seen is not None:
            seen.append(request)
        body = {"chunk_id": "ch_1", "released_id": 7} if status == 200 else {"detail": "no"}
        return httpx2.Response(status, json=body)

    return HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(handler)))


@pytest.mark.unit
def test_the_client_posts_the_release_and_reads_200_as_accepted() -> None:
    seen: list[httpx2.Request] = []

    assert _http(200, seen).release("ch_1", epoch=3, route_token="rtok", reason="throttled") is True

    (request,) = seen
    assert (request.method, request.url.path) == ("POST", "/api/fleet/chunks/ch_1/release")
    assert json.loads(request.content) == {"epoch": 3, "route_token": "rtok", "reason": "throttled"}


@pytest.mark.unit
@pytest.mark.parametrize("status", [409, 403, 404])
def test_the_client_reads_each_refusal_as_held(status: int) -> None:
    assert _http(status).release("ch_1", epoch=3, route_token=None, reason="throttled") is False


@pytest.mark.unit
def test_the_client_raises_on_a_hub_failure_and_a_transport_error() -> None:
    with pytest.raises(HubClientError):
        _http(500).release("ch_1", epoch=3, route_token=None, reason="throttled")

    def unreachable(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("refused", request=request)

    client = HttpHubClient(httpx2.Client(base_url="http://hub.test", transport=httpx2.MockTransport(unreachable)))
    with pytest.raises(HubClientError):
        client.release("ch_1", epoch=3, route_token=None, reason="throttled")


# --- between steps ---------------------------------------------------------------------------------------------------


def _ctx(
    store: SqlAlchemyRunnerStore,
    hub: FakeHub,
    throttle: ThrottleSettings | None,
    *,
    harness: FakeHarness | None = None,
    stdout_dir: Path | None = None,
) -> LoopContext:
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness if harness is not None else FakeHarness(handle=_HANDLE, verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(
            runner_name="r1",
            runner_id=FIXTURE_RUNNER_ID,
            bindings=serving(1),
            agents=1,
            subscription=_SLUG,
            throttle=throttle,
            worker_stdout_dir=str(stdout_dir) if stdout_dir is not None else "",
        ),
        subscriptions=(
            ResolvedSubscription(slug=_SLUG, name="Team", provider="anthropic", sample_interval_seconds=300),
        ),
    )


def _hold_floor(ctx: LoopContext) -> None:
    ctx.host_stores.usage_limits.record_usage_limit_mark(_SLUG, usage_limit_mark(_NOW - timedelta(minutes=1)))


def _advanced_chunk(store: SqlAlchemyRunnerStore, hub: FakeHub, *, first_node_ran: bool = True) -> None:
    """``ch_1`` moved onto its second node while this runner held its environment, with no lease for that node — the
    first node's lease closed in this binding's tenure, or (``first_node_ran=False``) a claim no lease ever followed."""
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    if first_node_ran:
        store.record_lease(
            NewLease(
                lease_id="lease_build",
                chunk_id="ch_1",
                graph_id="gr_1",
                node_id="nd_build",
                node_name="build",
                epoch=1,
                retries_max=2,
                created_at=_NOW,
            )
        )
        store.record_closure(
            lease_id="lease_build", chunk_id="ch_1", node_id="nd_build", reason="transitioned", closed_at=_NOW
        )
    store.set_route_token("ch_1", token="rtok", at=_NOW)
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id=FIXTURE_RUNNER_ID, latest_epoch=2
    )
    hub.envelopes["ch_1"] = make_envelope("ch_1", "review", node_id="nd_review", choices=_CHOICES, epoch=2)


@pytest.mark.component
def test_a_held_chunk_whose_next_step_is_declined_is_released_before_its_envelope_is_read() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, _ON_DECLINE)
    _advanced_chunk(store, hub)
    _hold_floor(ctx)

    HeldChunk(ctx, "ch_1").drive()

    assert hub.releases == [ReleaseCall("ch_1", 2, "rtok", "throttled")]
    assert hub.get_envelope_calls == []
    assert store.held_environment_ids() == []
    assert store.list_active_leases() == []


@pytest.mark.component
@pytest.mark.parametrize("throttle", [None, ThrottleSettings(), _ON_USAGE_LIMIT])
def test_a_held_chunk_on_a_runner_set_to_hold_waits_holding_its_environment(
    throttle: ThrottleSettings | None,
) -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, throttle)
    _advanced_chunk(store, hub)
    _hold_floor(ctx)

    HeldChunk(ctx, "ch_1").drive()

    assert hub.releases == []
    assert store.held_environment_ids() == ["e1"]
    assert store.list_active_leases() == []


@pytest.mark.component
def test_a_braked_runner_set_to_release_holds_its_chunk() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, _ON_DECLINE)
    _advanced_chunk(store, hub)
    store.record_local_pause(paused=True, at=_NOW, by="operator", report_kind="", report_payload="")

    HeldChunk(ctx, "ch_1").drive()

    assert hub.releases == []
    assert store.held_environment_ids() == ["e1"]


@pytest.mark.component
def test_an_admitted_step_on_a_runner_set_to_release_enters_its_node() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, _ON_DECLINE)
    _advanced_chunk(store, hub)

    HeldChunk(ctx, "ch_1").drive()

    assert hub.releases == []
    assert hub.get_envelope_calls == ["ch_1"]
    assert [lease.node_name for lease in store.list_active_leases()] == ["review"]


@pytest.mark.component
def test_a_refused_release_holds_and_the_step_waits_on_the_gate() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, _ON_DECLINE)
    _advanced_chunk(store, hub)
    _hold_floor(ctx)
    hub.release_refused.add("ch_1")

    HeldChunk(ctx, "ch_1").drive()
    HeldChunk(ctx, "ch_1").drive()

    assert len(hub.releases) == 1
    assert store.held_environment_ids() == ["e1"]
    assert store.list_active_leases() == []


@pytest.mark.component
@pytest.mark.parametrize("status", [404, 409, 403])
def test_a_hub_answering_the_route_with_a_refusal_leaves_the_runner_holding(status: int) -> None:
    """A hub that predates the route answers it ``404``: the runner keeps its environments and runs no completion."""
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = dataclasses.replace(_ctx(store, hub, _ON_DECLINE), hub=_http(status))
    _advanced_chunk(store, hub)
    _hold_floor(ctx)

    assert offer_between_steps(ctx, "ch_1", epoch=2, via="advance") is False

    assert store.held_environment_ids() == ["e1"]


@pytest.mark.component
def test_an_adopted_claim_whose_step_is_declined_is_released_before_its_envelope_is_read() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, _ON_DECLINE)
    _advanced_chunk(store, hub, first_node_ran=False)
    _hold_floor(ctx)

    InterruptedClaims(ctx).reconcile(braked=True)

    assert hub.releases == [ReleaseCall("ch_1", 2, "rtok", "throttled")]
    assert hub.get_envelope_calls == []
    assert store.held_environment_ids() == []


@pytest.mark.component
def test_an_adopted_claim_on_a_runner_set_to_hold_is_entered_as_before() -> None:
    store, hub = make_store("sqlite://"), FakeHub()
    ctx = _ctx(store, hub, None)
    _advanced_chunk(store, hub, first_node_ran=False)

    InterruptedClaims(ctx).reconcile()

    assert hub.releases == []
    assert hub.get_envelope_calls == ["ch_1"]
    assert len(store.list_active_leases()) == 1


# --- a usage-limited park --------------------------------------------------------------------------------------------


def _parked_lease(
    tmp_path: Path, throttle: ThrottleSettings | None
) -> tuple[SqlAlchemyRunnerStore, FakeHub, LoopContext]:
    """``lease_1``'s worker exited usage-limited and parked, holding the floor of the subscription it spends."""
    store, hub = make_store(f"sqlite:///{tmp_path / 'runner.db'}"), FakeHub()
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=100,
        process_start_time="start-100",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-a"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW, workspace_id="ws1")
    store.set_route_token("ch_1", token="rtok", at=_NOW)
    hub.chunks["ch_1"] = ChunkState(
        chunk_id="ch_1", status=ChunkStatus.RUNNING, route_runner_id=FIXTURE_RUNNER_ID, latest_epoch=1
    )
    limit = UsageLimit(resets_at=_NOW + timedelta(hours=2), detail="limited")
    harness = FakeHarness(handle=_HANDLE, verdict="pass", usage_limit=limit, usage=_SAMPLE)
    stdout_dir = tmp_path / "stdout"
    stdout_dir.mkdir()
    (stdout_dir / "lease_1.1.stdout").write_text("<envelope>")
    ctx = _ctx(store, hub, throttle, harness=harness, stdout_dir=stdout_dir)
    Advance(ctx).run()
    assert store.pause_parked_lease_ids() == {"lease_1"}
    return store, hub, ctx


_SAMPLE = UsageSample(
    kind="spawn",
    model="claude-x",
    input_tokens=9,
    output_tokens=1,
    cache_read_tokens=0,
    cache_create_tokens=0,
    cost_usd=0.1,
)


def _usage(store: SqlAlchemyRunnerStore) -> list[object]:
    """The usage facts booked so far, buffered for the hub."""
    return [fact for fact in store.pending_outbound() if fact.kind == USAGE_RECORDED]


def _on_unpause(store: SqlAlchemyRunnerStore, ctx: LoopContext, *, ask_parked: bool = False) -> None:
    lease = store.active_lease("lease_1")
    assert lease is not None
    DormantSession(ctx, lease).on_unpause(
        store.open_pause_parks()["lease_1"],
        None,
        bindings=store.bindings_for_chunk("ch_1"),
        ask_parked=ask_parked,
        brakes=RunnerBrakes(local=False, hub=False),
    )


@pytest.mark.component
def test_a_usage_limited_park_is_released_booking_its_usage_and_closing_its_lease(tmp_path: Path) -> None:
    store, hub, ctx = _parked_lease(tmp_path, _ON_USAGE_LIMIT)
    assert store.open_pause_parks()["lease_1"].cause == USAGE_LIMIT_PARK_CAUSE

    _on_unpause(store, ctx)

    assert hub.releases == [ReleaseCall("ch_1", 1, "rtok", "usage-limited")]
    assert store.list_active_leases() == []
    assert store.pause_parked_lease_ids() == set()
    assert store.held_environment_ids() == []
    assert [(c.lease.lease_id, c.reason) for c in store.list_closed_leases(5)] == [("lease_1", "released")]
    assert store.attempt_count("ch_1", "nd_build") == 1  # no retry spent
    assert len(_usage(store)) == 1


@pytest.mark.component
def test_a_refused_usage_limited_release_books_once_and_stays_parked(tmp_path: Path) -> None:
    store, hub, ctx = _parked_lease(tmp_path, _ON_USAGE_LIMIT)
    hub.release_refused.add("ch_1")

    _on_unpause(store, ctx)
    _on_unpause(store, ctx)

    assert len(hub.releases) == 1
    assert store.pause_parked_lease_ids() == {"lease_1"}
    assert store.held_environment_ids() == ["e1"]
    assert len(_usage(store)) == 1


@pytest.mark.component
@pytest.mark.parametrize("throttle", [None, _ON_DECLINE])
def test_a_usage_limited_park_on_a_runner_set_to_hold_stays_parked(
    tmp_path: Path, throttle: ThrottleSettings | None
) -> None:
    store, hub, ctx = _parked_lease(tmp_path, throttle)

    _on_unpause(store, ctx)

    assert hub.releases == []
    assert store.pause_parked_lease_ids() == {"lease_1"}


@pytest.mark.component
def test_an_operator_held_usage_limited_park_is_never_offered(tmp_path: Path) -> None:
    store, hub, ctx = _parked_lease(tmp_path, _ON_USAGE_LIMIT)
    hub.chunks["ch_1"] = dataclasses.replace(
        hub.chunks["ch_1"], pause=ChunkPause(by="operator", set_at=_NOW.isoformat())
    )

    _on_unpause(store, ctx)
    hub.chunks["ch_1"] = dataclasses.replace(hub.chunks["ch_1"], pause=None)
    _on_unpause(store, ctx, ask_parked=True)

    assert hub.releases == []
    assert store.pause_parked_lease_ids() == {"lease_1"}


@pytest.mark.component
def test_a_usage_limited_park_whose_floor_opened_wakes_instead(tmp_path: Path) -> None:
    store, hub, ctx = _parked_lease(tmp_path, _ON_USAGE_LIMIT)
    assert isinstance(ctx.clock, FixedClock)
    ctx.clock.advance(timedelta(hours=3))

    _on_unpause(store, ctx)

    assert hub.releases == []
    assert store.pause_parked_lease_ids() == set()
    assert [lease.lease_id for lease in store.list_active_leases()] == ["lease_1"]
