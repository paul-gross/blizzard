"""Landing invariants on the pre-policy delivery shape and its subsequent turns."""

from pathlib import Path

from blizzard.hub.store import schema as hub
from blizzard.tools.invariants import hub_invariants
from tests.support.invariant_checker_world import (
    _flow,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _forge_repository,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _hub_engine,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _link,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _open_turn,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _seed_at_deliver,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _submit,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
    _terms,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.invariant_checker_world
)
from tests.support.migrations.invariant_checker import NOW, insert
from tests.support.scaffolding import default_project, default_tenant, hub_scoped, stamp

_LANDING = {
    "hub:landing-position-on-link",
    "hub:submission-terms-standing",
    "hub:repository-references-in-tenant",
}


def test_carried_over_delivery_and_later_delivery_hold_landing_invariants(tmp_path: Path) -> None:
    engine = _hub_engine(tmp_path)
    tenant, project = default_tenant(engine), default_project(engine)
    with engine.begin() as conn:
        _forge_repository(conn, "repo_a", tenant=tenant, forge_id="frg_github")
        _link(conn, project, "repo_a", tenant=tenant)
        _flow(conn)
        for n in (1, 2):
            _seed_at_deliver(conn, f"ch_{n}")
            _submit(conn, f"fsub_{n}", chunk_id=f"ch_{n}", node_id="nd_deliver", arrival=n, flow_id="flw_main")
        # Before policies, a completed visit and a pending visit had no captured terms.
        for n, outcome in ((1, "landed"), (2, "pending")):
            conn.execute(
                insert(hub.hub_turns).values(
                    stamp(
                        hub.hub_turns,
                        turn_id=f"htn_{n}",
                        submission_id=f"fsub_{n}",
                        started_at=NOW,
                        ended_at=NOW,
                        outcome=outcome,
                    )
                )
            )
        conn.execute(
            insert(hub.flow_submission_closures).values(
                stamp(
                    hub.flow_submission_closures,
                    submission_id="fsub_1",
                    outcome="landed",
                    closed_at=NOW,
                )
            )
        )

    def assert_landing_invariants() -> None:
        assert [v for v in hub_invariants(engine).run() if v.invariant in _LANDING] == []

    assert_landing_invariants()
    with engine.begin() as conn:
        # The resumed visit captures terms before opening its next turn.
        _terms(conn, "fsub_2", "repo_a")
        _open_turn(conn, "htn_3", "fsub_2")
    assert_landing_invariants()
    with engine.begin() as conn:
        conn.execute(
            hub_scoped(hub.hub_turns.update().where(hub.hub_turns.c.turn_id == "htn_3")).values(
                ended_at=NOW, outcome="landed"
            )
        )
        conn.execute(
            insert(hub.flow_submission_closures).values(
                stamp(
                    hub.flow_submission_closures,
                    submission_id="fsub_2",
                    outcome="landed",
                    closed_at=NOW,
                )
            )
        )
        _seed_at_deliver(conn, "ch_3")
        _submit(conn, "fsub_3", chunk_id="ch_3", node_id="nd_deliver", arrival=3, flow_id="flw_main")
        _terms(conn, "fsub_3", "repo_a")
        _open_turn(conn, "htn_4", "fsub_3")
    assert_landing_invariants()
