"""A stale ingest admission cannot mint a lease after runner or host retirement."""

from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.chunk.ports.fence import Claimant
from blizzard.hub.domain.runners.host import HostAddition, HostLifecycleFact, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import hub_store_connections
from tests.test_load_all_facts_store import _T0, _mint, _store

pytestmark = pytest.mark.component


@pytest.mark.parametrize("target", ["host", "runner"])
def test_retirement_fences_mint_without_writing_facts(tmp_path: Path, target: str) -> None:
    chunks, engine, _ = _store(tmp_path)
    _mint(chunks, "chunk")
    scoped = hub_store_connections(engine)
    hosts, runners = HostRegistryStore(scoped), RunnerRegistryStore(scoped)
    hosts.add(HostAddition("host", "host", "hash", _T0, "test"))
    runners.add(RunnerAddition("runner", "runner", "hash", _T0, "test", host_id="host"))
    if target == "host":
        hosts.record_retirement("host", fact=HostLifecycleFact("host", True, _T0, "test"), at=_T0, by="test")
    else:
        runners.record_lifecycle("runner", retired=True, at=_T0, by="test")

    with pytest.raises(HostRetired if target == "host" else RunnerRetired, match="fact ingest"):
        chunks.route.record_lease_minted("chunk", epoch=1, claimant=Claimant("runner", "lease"), at=_T0)

    with scoped.read("verify") as conn:
        for table in (s.lease_facts, s.epoch_owners):
            assert conn.execute(scoped.select(table, sa.func.count())).scalar_one() == 0
    assert chunks.route.runner_high_water("runner") == 0
