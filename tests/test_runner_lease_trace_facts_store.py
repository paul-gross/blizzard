"""The lease trace-facts adapter (component tier) — facts written through the runner store's own writers,
read back closed-only, and assembled into the same spans the equivalent hand-built facts yield."""

from __future__ import annotations

import pytest

from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.leases.model import NewLease, WorkRefStamp
from blizzard.runner.lifecycle.judgement.checks import ExecutedCheck
from blizzard.runner.store.errors import RunnerStoreConnections
from blizzard.runner.store.internal.lease_trace_facts_store import LeaseTraceFactsStore
from blizzard.runner.tracing.assembly import assemble_lease
from blizzard.runner.tracing.facts import (
    CheckResultFact,
    ChecksRanFact,
    ContextSampleFact,
    LeaseTraceFacts,
    NudgeFact,
    OverloadFact,
    ParkFact,
    ParkResumeFact,
    PauseParkFact,
    PauseResumeFact,
    SessionEndFact,
    SpawnFact,
    TakeoverEndFact,
    TakeoverFact,
)
from blizzard.runner.usage.repository import InvocationCost
from tests import runner_trace_fixtures as fx
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_store_errors
from tests.support import count_queries

pytestmark = pytest.mark.component

_NODE_ID = "g1-build"
_HARNESS = "claude-code"
# Registered under the id the hand-built fixture's grant carries.
_REGISTERED = RunnerIdentity(fx.REGISTERED.runner_id, fx.REGISTERED.runner_name, fx.at(0))


def _store(tmp_path, *, registered: bool = True) -> SqlAlchemyRunnerStore:  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    if registered:
        store.record_runner_identity(_REGISTERED)
    return store


def _reader(store: SqlAlchemyRunnerStore) -> LeaseTraceFactsStore:
    return LeaseTraceFactsStore(RunnerStoreConnections(store._engine, runner_store_errors()))


def _session(lease_id: str, generation: int) -> SessionReference:
    return SessionReference(harness_id=_HARNESS, session_id=f"{lease_id}-sess-{generation}")


def _write_lease(store: SqlAlchemyRunnerStore, lease_id: str, chunk_id: str, *, close: bool = True) -> None:
    """One lease's life through every writer the trace facts are read from."""
    keys = {"lease_id": lease_id, "chunk_id": chunk_id}
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id=chunk_id,
            graph_id="g1",
            node_id=_NODE_ID,
            node_name="build",
            epoch=fx.EPOCH,
            retries_max=2,
            created_at=fx.at(0),
            session_name="builder",
            resolved_model="opus",
            resolved_effort="high",
            graph_name="flow",
            work_refs=(
                WorkRefStamp(source="github", ref="paul-gross/blizzard#745", label="blizzard#745"),
                WorkRefStamp(source="unconfigured", ref="x-1"),
            ),
        )
    )
    store.record_spawn(
        lease_id,
        pid=101,
        process_start_time="t",
        spawned_at=fx.at(1),
        session=_session(lease_id, 1),
        harness_version="2.1",
    )
    boundary = {**keys, "node_id": _NODE_ID, "epoch": fx.EPOCH, "start_position": None}
    store.record_boundary_open(**boundary, generation=1, kind="spawn", opened_at=fx.at(1))
    store.record_context_sample(**keys, context_tokens=5000, sampled_at=fx.at(10), session=_session(lease_id, 1))
    store.record_usage(
        **keys,
        node_id=_NODE_ID,
        epoch=fx.EPOCH,
        generation=1,
        sample=UsageSample(
            kind="spawn",
            model="claude-opus",
            input_tokens=100,
            output_tokens=10,
            cache_read_tokens=1000,
            cache_create_tokens=50,
            cost_usd=0.5,
        ),
        cost=InvocationCost(cost_usd=0.5, estimated_cost_usd=None),
        recorded_at=fx.at(20),
    )
    store.record_park(**keys, question_id="q-1", parked_at=fx.at(30))
    store.record_park_resume(lease_id=lease_id, question_id="q-1", resumed_at=fx.at(40))
    store.record_spawn(
        lease_id,
        pid=102,
        process_start_time="t",
        spawned_at=fx.at(40),
        session=_session(lease_id, 2),
        harness_version="2.1",
    )
    store.record_boundary_open(**boundary, generation=2, kind="resume", opened_at=fx.at(40))
    store.record_pause_park(lease_id=lease_id, chunk_id=chunk_id, parked_at=fx.at(50))
    store.record_pause_park_resume(lease_id=lease_id, resumed_at=fx.at(55))
    store.record_overload(
        **keys,
        epoch=fx.EPOCH,
        generation=2,
        invocation_kind="worker",
        invocation_identity="worker",
        streak_ordinal=1,
        observed_at=fx.at(60),
        resume_after=fx.at(65),
    )
    store.record_nudge_fired(lease_id=lease_id, epoch=fx.EPOCH, at=fx.at(70))
    store.record_session_end(lease_id=lease_id, ended_at=fx.at(80))
    store.record_check_results(
        **keys,
        node_id=_NODE_ID,
        epoch=fx.EPOCH,
        results=[ExecutedCheck(command="make test", passed=True, output_tail="ok")],
        at=fx.at(85),
    )
    store.record_checks_ran(lease_id=lease_id, epoch=fx.EPOCH, at=fx.at(85))
    takeover_id = f"tko_{lease_id}"
    store.record_takeover(
        takeover_id=takeover_id,
        chunk_id=chunk_id,
        lease_id=lease_id,
        workdir="/w",
        fence_epoch=None,
        opened_at=fx.at(88),
        session=_session(lease_id, 2),
    )
    store.record_takeover_end(takeover_id=takeover_id, ended_at=fx.at(90))
    if close:
        store.close_boundaries_for_lease(lease_id, reason="lease_closed", at=fx.at(100))
        store.record_closure(
            lease_id=lease_id, chunk_id=chunk_id, node_id=_NODE_ID, reason="transitioned", closed_at=fx.at(100)
        )


def _expected() -> LeaseTraceFacts:
    """The Phase-3 fixture equivalent of :func:`_write_lease` for :data:`fx.LEASE_ID`, row ids as first written."""

    def spawn(row_id: int, seconds: int) -> SpawnFact:
        return SpawnFact(
            id=row_id,
            spawned_at=fx.at(seconds),
            harness_id=_HARNESS,
            harness_version="2.1",
            session_id=f"{fx.LEASE_ID}-sess-{row_id}",
            identified_at=fx.at(seconds),
        )

    return fx.make_facts(
        spawns=(spawn(1, 1), spawn(2, 40)),
        boundaries=(fx.boundary(1, 1, "spawn", 1, 100), fx.boundary(2, 2, "resume", 40, 100)),
        usage=(fx.usage(1, 1, "spawn", 20),),
        session_ends=(SessionEndFact(1, fx.at(80)),),
        context_samples=(ContextSampleFact(1, fx.at(10), 5000),),
        parks=(ParkFact(1, "q-1", fx.at(30)),),
        park_resumes=(ParkResumeFact(1, "q-1", fx.at(40)),),
        pause_parks=(PauseParkFact(1, fx.at(50)),),
        pause_resumes=(PauseResumeFact(1, fx.at(55)),),
        overloads=(OverloadFact(1, 2, 1, fx.at(60), fx.at(65)),),
        takeovers=(TakeoverFact(f"tko_{fx.LEASE_ID}", fx.at(88)),),
        takeover_ends=(TakeoverEndFact(1, f"tko_{fx.LEASE_ID}", fx.at(90)),),
        nudges=(NudgeFact(1, fx.EPOCH, fx.at(70)),),
        check_results=(CheckResultFact(1, fx.EPOCH, True),),
        checks_ran=(ChecksRanFact(1, fx.EPOCH, fx.at(85)),),
    )


def test_written_facts_read_back_and_assemble_as_the_fixture(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _write_lease(store, fx.LEASE_ID, fx.CHUNK_ID)
    reader = _reader(store)

    read = reader.lease_trace_facts(fx.LEASE_ID)

    assert read is not None
    assert read == _expected()
    spans = assemble_lease(read)
    assert len(spans) > 2
    assert spans == assemble_lease(_expected())
    assert reader.lease_trace_facts_for([fx.LEASE_ID]) == {fx.LEASE_ID: read}


def test_an_open_or_unknown_lease_is_absent_from_both_forms(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _write_lease(store, fx.LEASE_ID, fx.CHUNK_ID)
    _write_lease(store, "lease_open", "ch_open", close=False)
    reader = _reader(store)

    assert reader.lease_trace_facts("lease_open") is None
    assert reader.lease_trace_facts("lease_unknown") is None
    assert set(reader.lease_trace_facts_for(["lease_open", "lease_unknown", fx.LEASE_ID])) == {fx.LEASE_ID}
    assert reader.lease_trace_facts_for([]) == {}


def test_facts_wait_for_the_first_registration_then_carry_its_id(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path, registered=False)
    _write_lease(store, fx.LEASE_ID, fx.CHUNK_ID)
    reader = _reader(store)

    assert reader.lease_trace_facts(fx.LEASE_ID) is None
    assert reader.lease_trace_facts_for([fx.LEASE_ID]) == {}

    store.record_runner_identity(RunnerIdentity("rn_01JREGISTERED", "r-claude", fx.at(200)))

    read = reader.lease_trace_facts(fx.LEASE_ID)
    assert read is not None
    assert (read.runner.runner_id, read.runner.runner_name) == ("rn_01JREGISTERED", "r-claude")
    assert reader.lease_trace_facts_for([fx.LEASE_ID])[fx.LEASE_ID].lease == read.lease


def test_each_lease_keeps_only_its_own_rows(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _write_lease(store, "lease_a", "ch_a")
    _write_lease(store, "lease_b", "ch_b")

    read = _reader(store).lease_trace_facts_for(["lease_a", "lease_b"])

    assert {f.takeovers[0].takeover_id for f in read.values()} == {"tko_lease_a", "tko_lease_b"}
    assert all(len(f.spawns) == 2 and len(f.takeover_ends) == 1 for f in read.values())
    assert read["lease_b"].spawns[0].session_id == "lease_b-sess-1"


def test_the_plural_read_issues_the_same_statements_for_one_lease_as_for_many(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    ids = [f"lease_{n}" for n in range(5)]
    for n, lease_id in enumerate(ids):
        _write_lease(store, lease_id, f"ch_{n}")
    reader = _reader(store)

    one = count_queries(store._engine, lambda: reader.lease_trace_facts_for(ids[:1]))
    many = count_queries(store._engine, lambda: reader.lease_trace_facts_for(ids))

    assert one == many
    assert len(reader.lease_trace_facts_for(ids)) == len(ids)
