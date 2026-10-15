"""A runner store's binding (component tier): a store reads as empty until it is bound, a runner is bound from its
configured id before its hub answers, registration adopts no id, and the writers that can run with no runner bound
hold off: ``PATCH /api/runner`` and ``POST /api/selftests`` answer 503 naming the cause."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.runner import runtime as runner_runtime
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.identity import IdentityState, RunnerIdentity
from blizzard.runner.loop.tick import tick
from blizzard.runner.loop_wiring import LoopWiring
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION, RunnerAlreadyBound, RunnerUnbound
from tests.host_files import load_runner_config, with_workspace
from tests.runner_client import runner_client
from tests.runner_fakes import FakeHarness, FakeHub, SqlAlchemyRunnerStore, loop_graph, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)


@dataclass
class _Binding:
    runner_id: str | None


def _unbound_store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=None)


def _write_rows(store: SqlAlchemyRunnerStore, runner_id: str, *chunk_ids: str) -> None:
    """Pause-park rows as the named runner wrote them."""
    with store._engine.begin() as conn:
        conn.execution_options(**{RUNNER_SCOPED_OPTION: True}).execute(
            sa.insert(schema.pause_parks),
            [{"lease_id": f"lease_{c}", "chunk_id": c, "parked_at": _NOW, "runner_id": runner_id} for c in chunk_ids],
        )


def test_binding_a_store_makes_its_runners_rows_visible(tmp_path: Path) -> None:
    store = _unbound_store(tmp_path)
    _write_rows(store, "rn_a", "ch_1")
    assert store.pause_parked_lease_ids() == set()  # unbound: reads answer as an empty store

    store.connections.bind("rn_a")

    assert store.pause_parked_lease_ids() == {"lease_ch_1"}


def test_an_unbound_store_refuses_the_identity_write_and_a_bound_one_another_runners(tmp_path: Path) -> None:
    store = _unbound_store(tmp_path)
    identity = RunnerIdentity("rn_a", "runner-local", _NOW)

    with pytest.raises(RunnerUnbound):
        store.record_runner_identity(identity)

    store.connections.bind("rn_b")
    with pytest.raises(RunnerAlreadyBound):
        store.record_runner_identity(identity)
    assert store.connections.runner_id == "rn_b"


def test_patch_runner_answers_503_naming_the_cause_until_bound(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with runner_client(create_app(config, binding=_Binding(None))) as client:
        resp = client.patch("/api/runner", json={"paused": True})

    assert resp.status_code == 503
    assert "bound to no runner id" in resp.json()["detail"]


def test_a_selftest_answers_503_until_bound_after_naming_an_unknown_harness(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    adapter = FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict=None)
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    with runner_client(create_app(config, harnesses=harnesses, binding=_Binding(None))) as client:
        refused = client.post("/api/selftests", json={"harness": CLAUDE_CODE_HARNESS_ID})
        unknown = client.post("/api/selftests", json={"harness": "codex"})

    assert refused.status_code == 503
    assert "bound to no runner id" in refused.json()["detail"]
    assert unknown.status_code == 422  # a misnamed harness is named whether or not the runner has registered


def _owners(engine: sa.Engine) -> dict[str, set[str]]:
    """The runners each non-empty runner-scoped table's rows name, per table."""
    scoped = [t for t in schema.metadata.sorted_tables if t.info["scope"] == "runner"]
    with engine.connect() as conn:
        conn = conn.execution_options(**{RUNNER_SCOPED_OPTION: True})
        owners = {t.name: set(conn.execute(sa.select(t.c.runner_id).distinct()).scalars()) for t in scoped}
    return {name: ids for name, ids in owners.items() if ids}


def test_a_runner_is_bound_from_configuration_before_its_hub_answers_and_registration_adopts_no_id(
    tmp_path: Path,
) -> None:
    runner_runtime.init_environment(tmp_path / "runner")
    config = with_workspace(load_runner_config(tmp_path / "runner"), root=str(tmp_path / "workspace"))
    hub = FakeHub(default_runner_id="rn_other")
    hub.down = True
    with loop_graph(config, runner_id="rn_a") as graph:
        assert graph.connections.runner_id == "rn_a"  # bound by its configured id, with the hub down
        ctx = LoopWiring(config, "", None).context(hub, graph)
        tick(ctx)
        assert "runner_identity" not in _owners(graph.engine)  # an unreachable hub records no identity

        hub.down = False
        tick(ctx)  # the hub answers that the token belongs to another runner: nothing is adopted
        assert graph.connections.runner_id == "rn_a"
        assert "runner_identity" not in _owners(graph.engine)
        assert graph.identity_verdict.verdict().state is IdentityState.MISMATCHED

        hub.default_runner_id = "rn_a"
        hub.issued[hub._token] = "rn_a"
        tick(ctx)  # the configured runner's token: its identity row is recorded, and every row names it

        owners = _owners(graph.engine)
        assert "runner_identity" in owners
        assert all(ids == {"rn_a"} for ids in owners.values()), owners
