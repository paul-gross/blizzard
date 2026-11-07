"""The garden delivery recorder a ``garden-deliver`` pass records through (component tier). Seeds a chunk via
``seed_work_item`` (its own resting chunk), records a run context through ``RunContextStore``, records its delta and
proposal artifacts through the artifacts repository, and delivers them through the recorder over the hub's store."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import insert, select

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.finding_store import FindingStore
from tests.support.garden_delivery_world import (
    _EPOCH,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.garden_delivery_world
    _NOW,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.garden_delivery_world
    _ROUTINE,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.garden_delivery_world
    SCOPE,
    Delivered,
    add_op,
    deliver,
    finding_count,
    garden_delta,
    record_artifact,
    seed_chunk,
    seed_scope,
)
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    capture_statements,
    default_project,
    hub_scoped,
    hub_store_connections,
    seed_lease,
    stamp,
)


def _seed_finding(
    hub: HubHarness,
    finding_id: str,
    *,
    scope_slug: str = SCOPE,
    routine_name: str = _ROUTINE,
    at: datetime = _NOW,
) -> None:
    FindingStore(hub_store_connections(hub.engine)).add(
        finding_id,
        project_id=default_project(hub.engine),
        routine_name=routine_name,
        scope_slug=scope_slug,
        class_="stale-docstring",
        locus="a.py:1",
        summary="s",
        introduced=None,
        at=at,
    )


def _seed_review_finding(hub: HubHarness, finding_id: str, *, scope_slug: str = SCOPE, at: datetime = _NOW) -> None:
    with hub.engine.begin() as conn:
        conn.execute(
            insert(s.findings).values(
                stamp(
                    s.findings,
                    finding_id=finding_id,
                    routine_name=None,
                    scope_slug=scope_slug,
                    class_="correctness",
                    locus="a.py:1",
                    summary="s",
                    source="review",
                    severity="should-fix",
                    raised_by_chunk_id="ch_review_1",
                )
            )
        )
        conn.execute(
            insert(s.finding_facts).values(stamp(s.finding_facts, finding_id=finding_id, kind="add", recorded_at=at))
        )


def _observed_op(finding_id: str) -> dict[str, object]:
    return {"op": "observed", "id": finding_id}


def _gone_op(finding_id: str, *, note: str = "n") -> dict[str, object]:
    return {"op": "gone", "id": finding_id, "note": note}


def _proposals(*, findings: list[str]) -> str:
    return json.dumps([{"ref": "p1", "class": "c", "title": "t", "body": "b", "findings": findings}])


# --- recorded --------------------------------------------------------------


def test_a_clean_addition_is_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered == Delivered("recorded")
    assert finding_count(hub) == 1


def test_additions_only_mints_every_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(
        hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(locus="a.py:1"), add_op(locus="b.py:2")])
    )

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "recorded"
    assert finding_count(hub) == 2


def test_transformations_only_mints_no_new_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "recorded"
    assert finding_count(hub) == 1  # the pre-seeded finding alone — nothing new minted


def test_a_mixed_delta_with_proposals_is_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(), _gone_op(finding_id)]))
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "recorded"
    assert finding_count(hub) == 2  # the pre-seeded one plus the freshly-added one
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all()


def test_a_docket_citing_its_own_runs_add_ref_materializes_with_the_minted_id(tmp_path: Path) -> None:
    """A docket citing only a submission-local `ref` from this delivery's own delta
    materializes, and the proposal's link row names the `fin_` id delivery itself
    minted, not the `ref` spelling."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(ref="new-1")]))
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=["new-1"]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        minted_finding_id = conn.execute(hub_scoped(select(s.findings.c.finding_id))).scalar_one()
        link_finding_ids = [row.finding_id for row in conn.execute(hub_scoped(select(s.garden_proposal_findings)))]
    assert link_finding_ids == [minted_finding_id]
    assert link_finding_ids != ["new-1"]


def test_a_docket_citing_a_prior_runs_live_id_still_works_unchanged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        link_finding_ids = [row.finding_id for row in conn.execute(hub_scoped(select(s.garden_proposal_findings)))]
    assert link_finding_ids == [finding_id]


def test_a_proposal_citing_no_findings_is_recorded_with_no_links(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        proposals = conn.execute(hub_scoped(select(s.garden_proposals))).all()
        assert len(proposals) == 1
        assert conn.execute(hub_scoped(select(s.garden_proposal_findings))).all() == []


def test_an_empty_docket_is_recorded(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))
    record_artifact(hub, chunk_id, name="docket", content="[]")

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered == Delivered("recorded")
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all() == []


def test_an_observed_op_admits_a_review_sourced_finding_on_the_runs_own_scope(tmp_path: Path) -> None:
    """The delivery's known-findings set is widened with review-sourced
    findings on the run's own scope, so an `observed`/`gone` op may transform one under
    the same same-scope constraint any routine-sourced finding is already held to."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_review_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "recorded"


def test_a_docket_can_cite_a_review_sourced_finding_on_the_runs_own_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_review_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        links = conn.execute(hub_scoped(select(s.garden_proposal_findings))).all()
    assert [link.finding_id for link in links] == [finding_id]


def test_a_review_sourced_finding_on_another_scope_is_out_of_scope(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    seed_scope(hub, "other-scope")
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_review_finding(hub, finding_id, scope_slug="other-scope")
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"


# --- invalid -----------------------------------------------------------------


def test_a_malformed_artifact_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content="not valid json")

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"
    assert "delta" in delivered.detail
    assert finding_count(hub) == 0


def test_a_delta_declaring_a_scope_other_than_the_runs_own_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    seed_scope(hub, "other-scope")
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(scope="other-scope", findings=[add_op()]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"
    assert "other-scope" in delivered.detail
    assert SCOPE in delivered.detail
    assert finding_count(hub) == 0


def test_a_repeated_delta_artifact_name_still_records_cleanly(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))

    delivered = deliver(hub, chunk_id, delta=["delta", "delta"])

    assert delivered == Delivered("recorded")
    assert finding_count(hub) == 1


def test_an_observed_op_revives_a_previously_gone_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    project = default_project(hub.engine)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    # Both prior facts precede the delivering run's instant, so the delivered `observed`
    # is genuinely newest — `derive_liveness` resolves on `recorded_at`, not row order.
    before = hub.clock.now() - timedelta(days=1)
    _seed_finding(hub, finding_id, at=before)
    FindingStore(hub_store_connections(hub.engine)).record_fact(finding_id, kind="gone", at=before, note="fixed")
    assert [f.finding_id for f in hub.services.findings.list_for_routine(project, _ROUTINE)] == []
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered == Delivered("recorded")
    # "Gone is reversible" is a liveness claim, not just a route outcome: the delivered
    # `observed` fact must actually restore the finding to the live set.
    assert [f.finding_id for f in hub.services.findings.list_for_routine(project, _ROUTINE)] == [finding_id]


def _deliver_review_finding(hub: HubHarness, finding_id: str) -> None:
    """A review-sourced finding on the run's scope, then a `delivered` fact strictly between
    its `add` and the delivering run's instant."""
    base = hub.clock.now() - timedelta(days=2)
    _seed_review_finding(hub, finding_id, at=base)
    FindingStore(hub_store_connections(hub.engine)).record_fact(
        finding_id,
        kind="delivered",
        at=base + timedelta(days=1),
        note="delivered by hub:1",
        actor="u_closer",
        proposal_id="gprop_1",
    )


def test_a_gone_op_settles_a_delivered_review_sourced_finding_to_resolved(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _deliver_review_finding(hub, finding_id)
    assert hub.services.findings.get(finding_id) is not None
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_gone_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered == Delivered("recorded")
    settled = hub.services.findings.get(finding_id)
    assert settled is not None
    assert settled.state == "resolved"
    assert settled.actor == "u_closer"


def test_an_observed_op_revives_a_delivered_review_sourced_finding_to_live(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _deliver_review_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered == Delivered("recorded")
    revived = hub.services.findings.get(finding_id)
    assert revived is not None
    assert revived.state == "live"


def test_an_unknown_finding_id_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    ghost = Id.mint(IdPrefix.FINDING, hub.clock).value
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(ghost)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"
    assert ghost in delivered.detail
    assert finding_count(hub) == 0


def test_an_out_of_scope_transformation_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    seed_scope(hub, "other-scope")
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id, scope_slug="other-scope")
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(scope=SCOPE, findings=[_observed_op(finding_id)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"
    assert "scope" in delivered.detail


def test_an_unresolved_delta_artifact_is_invalid_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)

    delivered = deliver(hub, chunk_id, delta=["ghost-artifact"])

    assert delivered.outcome == "invalid"
    assert "ghost-artifact" in delivered.detail


def test_a_proposals_artifact_naming_one_ref_twice_is_invalid(tmp_path: Path) -> None:
    """A duplicated `ref` collides on the very pair the store dedupes against, so it is
    rejected as a legible `invalid` the graph can bounce on — never a raw store fault the
    pass can only report as a failure."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    twice = json.dumps(
        [
            {"ref": "p1", "class": "c", "title": "one", "body": "b", "findings": [finding_id]},
            {"ref": "p1", "class": "c", "title": "two", "body": "b", "findings": [finding_id]},
        ]
    )
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())
    record_artifact(hub, chunk_id, name="docket", content=twice)

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "invalid"
    assert "docket" in delivered.detail and "p1" in delivered.detail
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all() == []


def test_a_docket_citing_the_same_finding_twice_is_invalid(tmp_path: Path) -> None:
    """A repeated citation collides on `garden_proposal_findings`'s own primary key —
    rejected here as a legible `invalid` rather than left to fail the insert raw."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id, finding_id]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "invalid"
    assert finding_id in delivered.detail
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all() == []


def test_a_docket_citing_a_ref_absent_from_the_delta_is_invalid_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(ref="new-1")]))
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=["ghost-ref"]))

    delivered = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])

    assert delivered.outcome == "invalid"
    assert "ghost-ref" in delivered.detail
    assert finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all() == []


def test_an_add_ops_ref_shaped_like_a_finding_id_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    disguised_ref = Id.mint(IdPrefix.FINDING, hub.clock).value
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(ref=disguised_ref)]))

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered.outcome == "invalid"
    assert disguised_ref in delivered.detail
    assert finding_count(hub) == 0


def test_an_add_ref_duplicated_across_the_deliverys_deltas_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta-a", content=garden_delta(findings=[add_op(locus="a.py:1", ref="new-1")]))
    record_artifact(hub, chunk_id, name="delta-b", content=garden_delta(findings=[add_op(locus="b.py:2", ref="new-1")]))

    delivered = deliver(hub, chunk_id, delta=["delta-a", "delta-b"])

    assert delivered.outcome == "invalid"
    assert "new-1" in delivered.detail
    assert finding_count(hub) == 0


# --- replay --------------------------------------------------------------


def test_a_replayed_delivery_still_reports_recorded_and_mints_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))

    first = deliver(hub, chunk_id, delta=["delta"])
    assert first == Delivered("recorded")
    assert finding_count(hub) == 1

    second = deliver(hub, chunk_id, delta=["delta"])

    assert second.outcome == "recorded"
    assert finding_count(hub) == 1  # nothing new minted on replay


def test_a_replayed_delivery_stays_recorded_even_after_one_of_its_findings_exits(tmp_path: Path) -> None:
    """A replay of an already-fully-materialized delivery must
    stay a no-op `recorded` (machinery.md §Delivery) even when a person exits a finding
    that delivery's own op named in the meantime — re-validating today's live state
    against yesterday's already-recorded content would otherwise turn the replay into a
    spurious `invalid`."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id, at=hub.clock.now())
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(finding_id)]))

    first = deliver(hub, chunk_id, delta=["delta"])
    assert first == Delivered("recorded")

    findings = FindingStore(hub_store_connections(hub.engine))
    finding = findings.get(finding_id)
    assert finding is not None
    hub.services.finding_exit.resolve([finding], note="landed", actor="u_1")
    resolved = findings.get(finding_id)
    assert resolved is not None and resolved.state == "resolved"

    second = deliver(hub, chunk_id, delta=["delta"])

    assert second.outcome == "recorded"


def test_a_later_delivery_adding_a_second_delta_still_lands_the_new_ones_findings(tmp_path: Path) -> None:
    """`--delta a`, then `--delta a --delta b` at a fresh epoch: `b`'s findings land
    beside `a`'s, which are not minted twice — an already-materialized artifact in the
    call may not drop the rest of it."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta-a", content=garden_delta(findings=[add_op(locus="a.py:1")]))
    record_artifact(hub, chunk_id, name="delta-b", content=garden_delta(findings=[add_op(locus="b.py:2")]))

    first = deliver(hub, chunk_id, delta=["delta-a"])
    assert first == Delivered("recorded")

    second = deliver(hub, chunk_id, delta=["delta-a", "delta-b"], epoch=_EPOCH + 1)

    assert second.outcome == "recorded"
    with hub.engine.begin() as conn:
        assert sorted(r.locus for r in conn.execute(hub_scoped(select(s.findings)))) == ["a.py:1", "b.py:2"]
        set_rows = conn.execute(hub_scoped(select(s.finding_sets))).all()
        assert len(set_rows) == 2  # one per delivered artifact, `a`'s recorded once


def test_a_later_delivery_re_carrying_the_same_proposals_mints_no_duplicate(tmp_path: Path) -> None:
    """Unlike a delta (deduped on `finding_sets.artifact_id`), a proposal has its own
    idempotence key — `docket`'s `p1` must land exactly once across both visits."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    finding_id = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, finding_id)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta())  # a clean delta
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=[finding_id]))

    first = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])
    assert first == Delivered("recorded")
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all().__len__() == 1

    second = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"], epoch=_EPOCH + 1)

    assert second.outcome == "recorded"
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.garden_proposals))).all().__len__() == 1  # not minted twice


def test_a_republished_dockets_ref_citation_still_resolves_after_its_delta_is_dropped(tmp_path: Path) -> None:
    """A worker that never saw a `recorded` response may resubmit at a fresh epoch. The
    delta is then dropped as already-materialized, but the docket's citation of `add`'s
    own ref must still land on the finding that first visit actually minted."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op(ref="F1")]))
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=["F1"]))

    first = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"])
    assert first == Delivered("recorded")
    assert finding_count(hub) == 1
    with hub.engine.begin() as conn:
        minted_finding_id = conn.execute(hub_scoped(select(s.findings.c.finding_id))).scalar_one()

    # Republish only the docket, at a fresh epoch — a new artifact, same `ref`.
    record_artifact(hub, chunk_id, name="docket", content=_proposals(findings=["F1"]), epoch=_EPOCH + 1)
    second = deliver(hub, chunk_id, delta=["delta"], proposals=["docket"], epoch=_EPOCH + 1)

    assert second.outcome == "recorded"
    assert finding_count(hub) == 1  # the delta is unchanged — nothing new minted
    with hub.engine.begin() as conn:
        proposals = conn.execute(hub_scoped(select(s.garden_proposals))).all()
        assert len(proposals) == 2  # a genuinely new proposal artifact each visit
        links = conn.execute(hub_scoped(select(s.garden_proposal_findings))).all()
        assert [link.finding_id for link in links] == [minted_finding_id, minted_finding_id]


# --- one bucket, two readers ------------------------------------------------


def test_every_finding_the_worker_read_shows_is_a_citable_proposal_finding(tmp_path: Path) -> None:
    """The worker read and delivery take one bucket: each id the read returns — a
    neighbour scope's, a review finding — is admitted as a proposal citation, while an
    `observed` op on a neighbour-scope id is refused as outside the declared scope."""
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    own = Id.mint(IdPrefix.FINDING, hub.clock).value
    neighbour = Id.mint(IdPrefix.FINDING, hub.clock).value
    review = Id.mint(IdPrefix.FINDING, hub.clock).value
    _seed_finding(hub, own)
    seed_scope(hub, "other-scope")
    _seed_finding(hub, neighbour, scope_slug="other-scope")
    _seed_review_finding(hub, review)

    shown = [row["finding_id"] for row in hub.client.get(f"/api/fleet/chunks/{chunk_id}/garden/findings").json()]
    assert set(shown) == {own, neighbour, review}

    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[_observed_op(neighbour)]))
    refused = deliver(hub, chunk_id, delta=["delta"])
    assert refused.outcome == "invalid"
    assert "outside the declared scope" in refused.detail

    record_artifact(hub, chunk_id, name="delta", content=garden_delta(), epoch=_EPOCH + 1)
    record_artifact(hub, chunk_id, name="proposals", content=_proposals(findings=shown), epoch=_EPOCH + 1)
    admitted = deliver(hub, chunk_id, delta=["delta"], proposals=["proposals"], epoch=_EPOCH + 1)
    assert admitted == Delivered("recorded")


# --- fenced ------------------------------------------------------------------


def test_a_delivery_for_a_stopped_chunk_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    delivered = deliver(hub, chunk_id, delta=["delta"])

    assert delivered == Delivered("fenced")
    assert finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert (
            conn.execute(hub_scoped(select(s.artifacts).where(s.artifacts.c.name == "garden-delivered"))).first()
            is None
        )


def test_a_delivery_a_restart_superseded_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_scope(hub, SCOPE)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, name="delta", content=garden_delta(findings=[add_op()]))
    seed_lease(hub.engine, chunk_id, epoch=2, runner_id="r1", at=hub.clock.now())

    delivered = deliver(hub, chunk_id, delta=["delta"], epoch=1)

    assert delivered == Delivered("fenced")
    assert finding_count(hub) == 0


def test_artifact_lookups_are_flat_as_the_delta_names_grow(tmp_path: Path) -> None:
    def artifact_selects(sub: str, count: int) -> int:
        (tmp_path / sub).mkdir()
        hub = build_hub(tmp_path / sub)
        seed_scope(hub, SCOPE)
        chunk_id = seed_chunk(hub)
        names = [f"delta-{i}" for i in range(count)]
        for name in names:
            record_artifact(hub, chunk_id, name=name, content=garden_delta())
        with capture_statements(hub.engine) as statements:
            delivered = deliver(hub, chunk_id, delta=names, proposals=["missing"])
        assert delivered.outcome == "recorded"
        return sum(1 for statement, _ in statements if "FROM artifacts" in statement)

    assert artifact_selects("one", 1) == artifact_selects("many", 5)
