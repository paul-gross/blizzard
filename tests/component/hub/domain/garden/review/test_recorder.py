"""The review-findings recorder a ``record-findings`` pass records through (component tier). Seeds a chunk via
``seed_work_item`` (its own resting chunk), records its ``review-finding-delta`` artifact through the artifacts
repository, and delivers it through the recorder over the hub's store — the
``tests/component/hub/domain/garden/delivery/test_recorder.py`` shape, minus the run context this recorder does not
need."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from blizzard.hub.store import schema as s
from tests.support.review_recorder_world import (
    ARTIFACT_NAME,
    EPOCH,
    Delivered,
    deliver,
    finding_count,
    record_artifact,
    seed_chunk,
)
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_project,
    hub_scoped,
    project_record,
    seed_lease,
    seed_project,
)


def _deferred(ref: str = "F1", *, scope: str = "blizzard", severity: str = "should-fix") -> dict[str, object]:
    return {
        "ref": ref,
        "disposition": "deferred",
        "severity": severity,
        "scope": scope,
        "class": "correctness",
        "locus": "a.py:1",
        "summary": "s",
    }


def _fixed(ref: str = "F1") -> dict[str, object]:
    return {"ref": ref, "disposition": "fixed"}


def _refuted(ref: str = "F1") -> dict[str, object]:
    return {"ref": ref, "disposition": "refuted"}


def _marker(hub: HubHarness, chunk_id: str) -> str:
    with hub.engine.begin() as conn:
        return conn.execute(
            hub_scoped(
                select(s.artifacts.c.data).where(
                    (s.artifacts.c.chunk_id == chunk_id) & (s.artifacts.c.name == "review-findings-delivered")
                )
            )
        ).scalar_one()


def _delta(entries: list[dict[str, object]]) -> str:
    return json.dumps({"entries": entries})


# --- recorded --------------------------------------------------------------


def test_a_deferred_entry_is_recorded_as_a_review_sourced_finding(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    delivered = deliver(hub, chunk_id)

    assert delivered == Delivered("recorded")
    with hub.engine.begin() as conn:
        row = conn.execute(hub_scoped(select(s.findings))).one()
    assert row.source == "review"
    assert row.severity == "should-fix"
    assert row.raised_by_chunk_id == chunk_id
    assert row.routine_name is None


def test_a_review_finding_takes_the_reviewed_chunks_project(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    first = project_record(hub, default_project(hub.engine))
    second = seed_project(hub.engine, "second", at=first.created_at + timedelta(days=1))
    third = seed_project(hub.engine, "third", at=first.created_at + timedelta(days=2))
    # The chunk's project is neither end of the tenant's, by id as by creation time.
    assert first.project_id < second < third
    chunk_id = seed_chunk(hub, project_id=second)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    delivered = deliver(hub, chunk_id)

    assert delivered == Delivered("recorded")
    with hub.engine.begin() as conn:
        project_id = conn.execute(hub_scoped(select(s.findings.c.project_id))).scalar_one()
    assert project_id == second


def test_fixed_and_refuted_entries_mint_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _refuted(ref="F2")]))

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    assert finding_count(hub) == 0


def test_a_mixed_delta_mints_only_its_deferred_entries(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred(ref="F1"), _fixed(ref="F2"), _refuted(ref="F3")]))

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    assert finding_count(hub) == 1


def test_an_unnamed_scope_is_minted_on_delivery(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred(scope="brand-new")]))

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "recorded"
    with hub.engine.begin() as conn:
        assert conn.execute(hub_scoped(select(s.scopes).where(s.scopes.c.slug == "brand-new"))).one()


# --- the marker ----------------------------------------------------------


def test_the_marker_names_the_finding_each_deferred_entry_minted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _deferred(ref="F2", scope="brand-new")]))

    deliver(hub, chunk_id)

    with hub.engine.begin() as conn:
        finding_id = conn.execute(hub_scoped(select(s.findings.c.finding_id))).scalar_one()
    assert json.loads(_marker(hub, chunk_id)) == {
        "outcome": "minted",
        "findings": [{"ref": "F2", "finding_id": finding_id, "scope_slug": "brand-new"}],
    }


def test_the_marker_says_none_were_deferred_when_the_delta_deferred_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _refuted(ref="F2")]))

    deliver(hub, chunk_id)

    assert json.loads(_marker(hub, chunk_id)) == {"outcome": "none-deferred", "findings": []}


def test_a_replay_over_a_newer_delta_leaves_the_marker_unchanged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    deliver(hub, chunk_id)
    written = _marker(hub, chunk_id)
    record_artifact(hub, chunk_id, content=_delta([_deferred(ref="F9")]), epoch=EPOCH + 1)

    deliver(hub, chunk_id, epoch=EPOCH + 1)

    assert _marker(hub, chunk_id) == written


# --- invalid -----------------------------------------------------------------


def test_a_malformed_artifact_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content="not valid json")

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert ARTIFACT_NAME in delivered.detail
    assert finding_count(hub) == 0


def test_a_deferred_entry_marked_blocking_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred(severity="blocking")]))

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert "blocking" in delivered.detail
    assert finding_count(hub) == 0


def test_a_duplicate_ref_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_fixed(ref="F1"), _deferred(ref="F1")]))

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert "F1" in delivered.detail
    assert finding_count(hub) == 0


def test_no_artifact_at_all_is_invalid(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)

    delivered = deliver(hub, chunk_id)

    assert delivered.outcome == "invalid"
    assert ARTIFACT_NAME in delivered.detail


# --- replay --------------------------------------------------------------


def test_a_replayed_delivery_still_reports_recorded_and_mints_nothing_new(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))

    first = deliver(hub, chunk_id)
    assert first == Delivered("recorded")
    assert finding_count(hub) == 1

    second = deliver(hub, chunk_id)

    assert second.outcome == "recorded"
    assert finding_count(hub) == 1


def test_a_replay_at_a_fresh_epoch_stays_a_no_op(tmp_path: Path) -> None:
    """Idempotence is keyed on the chunk alone, so even a fresh node/epoch visit for
    an already-delivered chunk mints nothing new."""
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    first = deliver(hub, chunk_id)
    assert first == Delivered("recorded")

    second = deliver(hub, chunk_id, epoch=EPOCH + 1)

    assert second.outcome == "recorded"
    assert finding_count(hub) == 1


# --- fenced ------------------------------------------------------------------


def test_a_delivery_for_a_stopped_chunk_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    assert hub.client.post(f"/api/chunks/{chunk_id}/stop", json={"by": "operator"}).status_code == 202

    delivered = deliver(hub, chunk_id)

    assert delivered == Delivered("fenced")
    assert finding_count(hub) == 0
    with hub.engine.begin() as conn:
        assert (
            conn.execute(
                hub_scoped(select(s.artifacts).where(s.artifacts.c.name == "review-findings-delivered"))
            ).first()
            is None
        )


def test_a_delivery_a_restart_superseded_is_fenced_and_lands_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = seed_chunk(hub)
    record_artifact(hub, chunk_id, content=_delta([_deferred()]))
    seed_lease(hub.engine, chunk_id, epoch=2, runner_id="r1", at=hub.clock.now())

    delivered = deliver(hub, chunk_id, epoch=1)

    assert delivered == Delivered("fenced")
    assert finding_count(hub) == 0
