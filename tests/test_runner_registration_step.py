"""The registration step and the gates it opens — the runner's identity comes from its hub, never
from its configuration or its store, and claiming, reaping, resuming and reconciling wait for it."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock, SystemClock
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessRegistry
from blizzard.runner.hub.identity import RunnerIdentityHolder
from blizzard.runner.leases import HEARTBEAT_STALENESS_THRESHOLD, NewLease
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopConfig, LoopContext
from blizzard.runner.loop.steps import Fill, Pull, Reap, Resume
from blizzard.runner.status.view import RunnerStatusService
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)

pytestmark = pytest.mark.component


def _ctx(db: Path, hub: FakeHub, *, name: str = "r-claude", registered: bool = False) -> LoopContext:
    """A context over the store at ``db``, its holder seeded from the store's identity row as
    composition seeds it at boot — so a second call over the same file is a restart."""
    store = make_store(f"sqlite:///{db}")
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        config=LoopConfig(runner_name=name, workspace_id="ws1", max_agents=1),
        registered=registered,
    )
    return type(ctx)(**{**ctx.__dict__, "identity": RunnerIdentityHolder(store.runner_identity())})


def _status_name(ctx: LoopContext) -> tuple[str | None, str]:
    store = ctx.stores.pause
    summary = RunnerStatusService(
        SystemClock(),
        pause=store,
        lease_record=ctx.stores.lease_record,
        outbound=ctx.stores.outbound,
        environments=ctx.stores.environments,
        asks=ctx.stores.asks,
        takeover=ctx.stores.takeover,
        escalations=ctx.stores.escalations,
        identity=ctx.identity,
        runner_name=ctx.config.runner_name,
        workspace_id="ws1",
        max_agents=1,
        hub_url="http://hub.example",
        env_pool=("e1",),
        workspace_root="",
        harnesses=HarnessRegistry({}),
    ).summary()
    return summary.runner_id, summary.runner_name


def test_a_runner_that_never_registered_claims_nothing_while_the_hub_is_unreachable(tmp_path: Path) -> None:
    hub = FakeHub()
    hub.down = True
    ctx = _ctx(tmp_path / "runner.db", hub)

    Pull(ctx).run()
    Fill(ctx).run()

    assert ctx.identity.current() is None
    assert ctx.stores.identity.runner_identity() is None
    assert hub.peek_queue_calls == 0  # not even a peek before the first registration
    assert _status_name(ctx) == (None, "r-claude")


def test_the_first_registration_records_the_hub_minted_identity_and_opens_the_claim_gate(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    ctx = _ctx(tmp_path / "runner.db", hub)

    Pull(ctx).run()
    Fill(ctx).run()

    held = ctx.identity.current()
    assert held is not None and (held.runner_id, held.runner_name) == ("rn_minted", "r-claude")
    assert ctx.stores.identity.runner_identity() == held
    assert hub.registered == [("r-claude", "ws1")]
    assert hub.peek_queue_calls == 1


def test_a_registered_runner_keeps_its_identity_and_mirrored_pause_across_a_restart_with_the_hub_unreachable(
    tmp_path: Path,
) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    hub.paused = True
    Registration(_ctx(tmp_path / "runner.db", hub)).run()

    hub.down = True
    restarted = _ctx(tmp_path / "runner.db", hub)
    Pull(restarted).run()

    held = restarted.identity.current()
    assert held is not None and held.runner_id == "rn_minted"
    assert restarted.stores.pause.hub_paused() is True


@pytest.mark.parametrize("paused", [False, True], ids=["live", "paused"])
def test_a_rename_by_restart_reaches_the_identity_row_the_holder_and_the_status_view(
    tmp_path: Path, paused: bool
) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    hub.paused = paused
    Registration(_ctx(tmp_path / "runner.db", hub, name="runner-local")).run()

    renamed = _ctx(tmp_path / "runner.db", hub, name="r-claude")
    assert _status_name(renamed) == ("rn_minted", "runner-local")  # the old name until the hub records the new one
    Registration(renamed).run()

    held = renamed.identity.current()
    assert held is not None and (held.runner_id, held.runner_name) == ("rn_minted", "r-claude")
    assert renamed.stores.identity.runner_identity() == held
    assert _status_name(renamed) == ("rn_minted", "r-claude")
    assert renamed.stores.pause.hub_paused() is paused


def test_a_rebuilt_store_learns_the_same_id_back_from_the_same_token(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    Registration(_ctx(tmp_path / "first.db", hub)).run()

    rebuilt = _ctx(tmp_path / "rebuilt.db", hub)
    Registration(rebuilt).run()

    held = rebuilt.stores.identity.runner_identity()
    assert held is not None and held.runner_id == "rn_minted"


def test_a_token_the_hub_does_not_hold_registers_nothing_and_claims_nothing(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    hub.issued.clear()  # the hub's data was reset under the runner
    ctx = _ctx(tmp_path / "runner.db", hub)

    Pull(ctx).run()
    Fill(ctx).run()

    assert ctx.stores.identity.runner_identity() is None
    assert hub.registered == []
    assert hub.peek_queue_calls == 0


def test_the_identity_holds_the_name_the_hub_answers_over_the_one_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hub that keeps the name it holds answers that one; the runner records the hub's."""
    hub = FakeHub(default_runner_id="rn_minted")
    declare = hub.register_runner
    monkeypatch.setattr(hub, "register_runner", lambda *a, **kw: replace(declare(*a, **kw), runner_name="r-held"))
    ctx = _ctx(tmp_path / "runner.db", hub, name="r-claude")

    Registration(ctx).run()

    held = ctx.identity.current()
    assert held is not None and (held.runner_id, held.runner_name) == ("rn_minted", "r-held")
    assert ctx.stores.identity.runner_identity() == held


def test_only_a_new_or_changed_identity_is_logged_at_info_though_every_tick_registers(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="rn_minted")
    with capture_logs() as logs:
        Registration(_ctx(tmp_path / "runner.db", hub, name="runner-local")).run()
        restarted = _ctx(tmp_path / "runner.db", hub, name="runner-local")
        Registration(restarted).run()
        Registration(restarted).run()
        Registration(_ctx(tmp_path / "runner.db", hub, name="r-claude")).run()

    info = [(e["event"], e.get("was_runner_name"), e.get("runner_name")) for e in logs if e["log_level"] == "info"]
    assert info == [
        ("runner identity changed", None, "runner-local"),
        ("runner identity changed", "runner-local", "r-claude"),
    ]


_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_LATER = _NOW + HEARTBEAT_STALENESS_THRESHOLD + timedelta(minutes=5)


def _running_lease(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str, *, pid: int, env: str) -> None:
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease_id,
        pid=pid,
        process_start_time=f"start-{pid}",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, f"sess-{pid}"),
        spawned_at=_NOW,
    )
    store.record_binding(chunk_id=chunk_id, environment_id=env, workdir=f"/ws/{env}", bound_at=_NOW)


def test_reap_and_resume_wait_for_the_first_registration_then_proceed(tmp_path: Path) -> None:
    """A store with no identity row, as the identity migration leaves one: whether a lease's route is
    still this runner's is unknowable, so nothing is reaped or abandoned until a registration records
    the id — not even once the hub answers again, since REAP and RESUME tick before PULL registers."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _running_lease(store, "lease_stalled", "ch_1", pid=100, env="e1")
    store.record_heartbeat(lease_id="lease_stalled", beat_at=_NOW)  # stale by _LATER, its worker alive
    _running_lease(store, "lease_marked", "ch_2", pid=200, env="e2")
    store.record_resume_intent(lease_id="lease_marked", marked_at=_NOW)
    hub = FakeHub(default_runner_id="rn_minted")
    hub.ended = {"ch_1"}  # a retry re-reads nothing, so a reap only closes and releases
    hub.not_found = {"ch_2"}  # judged, the marked lease's chunk is gone: an abandon
    probe = FakeProbe(alive={(100, "start-100")})
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=probe,
        clock=FixedClock(_LATER),
        registered=False,
    )

    hub.down = True
    Reap(ctx).run()
    Resume(ctx).run()
    Registration(ctx).run()
    hub.down = False
    Reap(ctx).run()
    Resume(ctx).run()

    assert ctx.identity.current() is None
    assert {lease.lease_id for lease in store.list_active_leases()} == {"lease_stalled", "lease_marked"}
    assert (probe.killed, store.resume_intent_lease_ids()) == ([], {"lease_marked"})

    Registration(ctx).run()
    Reap(ctx).run()
    Resume(ctx).run()

    assert probe.killed == [100]
    assert store.list_active_leases() == []
    assert store.resume_intent_lease_ids() == set()


def _refused_registration(store: SqlAlchemyRunnerStore) -> LoopContext:
    """A context over ``store``, its clock past every seeded instant, whose hub refuses the registration
    yet answers chunk reads, each chunk routed to ``rn_minted`` — the id the runner would have learned."""
    hub = FakeHub(default_runner_id="rn_minted")
    hub.issued.clear()
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1", "e2": "/ws/e2"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        clock=FixedClock(_LATER),
        registered=False,
    )


def test_pull_abandons_no_lease_and_closes_no_escalation_before_the_first_registration(tmp_path: Path) -> None:
    """Holding no id, the runner would read every route as another runner's."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _running_lease(store, "lease_live", "ch_1", pid=100, env="e1")
    _running_lease(store, "lease_parked", "ch_2", pid=200, env="e2")
    store.record_closure(
        lease_id="lease_parked", chunk_id="ch_2", node_id="nd_build", reason="escalated", closed_at=_NOW
    )
    ctx = _refused_registration(store)

    Pull(ctx).run()

    assert ctx.identity.current() is None
    assert [lease.lease_id for lease in store.list_active_leases()] == ["lease_live"]
    assert [escalation.chunk_id for escalation in store.open_escalations()] == ["ch_2"]


def test_fill_releases_no_interrupted_claim_before_the_first_registration(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_binding(chunk_id="ch_1", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)
    ctx = _refused_registration(store)

    Fill(ctx).run()

    assert [binding.chunk_id for binding in ctx.stores.environments.held_bindings()] == ["ch_1"]
