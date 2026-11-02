"""Stale fact-ingest admission cannot write usage after retirement."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.runners.host import HostAddition, HostLifecycleFact, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import hub_store_connections
from tests.test_load_all_facts_store import _T0, _store

pytestmark = pytest.mark.component


@pytest.mark.parametrize("target", ["host", "runner"])
def test_retirement_fences_usage_but_not_fact_acknowledgements(tmp_path: Path, target: str) -> None:
    chunks, engine, _ = _store(tmp_path)
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "hash", _T0, "test"))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test", host_id="host"))
    if target == "host":
        hosts.record_retirement("host", fact=HostLifecycleFact("host", True, _T0, "test"), at=_T0, by="test")
    else:
        runners.record_lifecycle("runner", retired=True, at=_T0, by="test")

    error = HostRetired if target == "host" else RunnerRetired
    actions = [
        lambda: runners.record_external_usage("runner", slug="s", name="S", sampled_at=_T0, windows_json="[]", at=_T0),
        lambda: runners.record_external_usage_miss("runner", slug="s", name="S", missed_at=_T0, reason="x", at=_T0),
        lambda: runners.record_local_pause("runner", paused=True, at=_T0, by="runner"),
    ]
    if target == "host":
        actions += [
            lambda: hosts.record_external_usage("host", slug="s", name="S", sampled_at=_T0, windows_json="[]", at=_T0),
            lambda: hosts.record_external_usage_miss("host", slug="s", name="S", missed_at=_T0, reason="x", at=_T0),
        ]
    for action in actions:
        with pytest.raises(error):
            action()
    with scoped.read("verify") as conn:
        for table in (
            s.runner_external_usage,
            s.runner_external_usage_misses,
            s.runner_local_pause_facts,
            s.host_external_usage,
            s.host_external_usage_misses,
        ):
            assert conn.execute(scoped.select(table, sa.func.count())).scalar_one() == 0
    chunks.route.set_runner_high_water("runner", seq=2, at=_T0)
    chunks.route.set_runner_high_water("runner", seq=1, at=_T0)
    assert chunks.route.runner_high_water("runner") == 2
    if target == "host":
        hosts.set_high_water("host", seq=2, at=_T0)
        hosts.set_high_water("host", seq=1, at=_T0)
        assert hosts.high_water("host") == 2
