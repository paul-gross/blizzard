"""Proposal acceptance selects its graph after concurrent lifecycle authoring."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from tests.support import build_hub, default_project, default_tenant, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("omit", [False, True], ids=["locked", "negative-control"])
def test_accept_waits_for_retirement_before_selecting_default(database_url, tmp_path, omit):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "proposal_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "proposal_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        hub = build_hub(tmp_path, engine=waiter, link=False, repositories=[])
        scoped = scoped_connections(holder, default_tenant(holder))
        graph = hub.services.graph_mint.ensure_default(PACKAGED.default.doc, definition_yaml=PACKAGED.default.text)
        GardenProposalStore(scoped).create(
            "proposal",
            project_id=default_project(holder),
            origin=GardenProposalOrigin.OPERATOR,
            created_by="test",
            routine_name=None,
            class_="c",
            title="t",
            body="b",
            findings=[],
            at=_AT,
        )
        proposal = hub.services.garden_proposals.get("proposal")
        assert proposal is not None
        with ThreadPoolExecutor(max_workers=1) as pool, scoped.read("retire") as conn:
            lock_config_authoring(conn)
            conn.execute(
                scoped.insert(s.graph_lifecycle_facts).values(
                    graph_id=graph.graph_id, retired=True, set_at=_AT, set_by="test"
                )
            )
            with patch.object(
                chunk_exclusive_store, "lock_config_authoring", (lambda conn: None) if omit else lock_config_authoring
            ):
                future = pool.submit(
                    hub.services.garden_proposal_closure.accept,
                    proposal,
                    reason=None,
                    by="test",
                    body=None,
                    mint=True,
                    findings=[],
                )
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='proposal_waiter' AND h.application_name='proposal_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("acceptance neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        assert future.result(timeout=5).chunk_id is not None
                finally:
                    conn.commit()
                if not omit:
                    with pytest.raises(DefaultGraphRetired):
                        future.result(timeout=5)
        closure = hub.services.garden_proposal_closures.get("proposal")
        assert (closure is not None) is omit
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
