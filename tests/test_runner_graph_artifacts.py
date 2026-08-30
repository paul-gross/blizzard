"""The runner's own mirror of a graph mint's ``artifacts:`` declarations — pinned at
lease mint, insert-if-absent, keyed on the mint's own ``graph_id`` rather than the lease.
``Spawner._mint`` is the only write site, ordered before ``record_lease``."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import SQLAlchemyError

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.steps import Fill
from blizzard.runner.store.repository import GraphArtifactRecord
from blizzard.wire.envelope import GraphArtifact
from blizzard.wire.queue import QueuePeekEntry
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    claimed_outcome,
    make_context,
    make_envelope,
    make_store,
)

_NOW = datetime(2026, 8, 17, 12, 0, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]
_HANDLE = WorkerHandle(session_id="sess-a", pid=100, process_start_time="start-100")


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _fill_ctx(store, env):  # type: ignore[no-untyped-def]
    hub = FakeHub()
    hub.queue = [QueuePeekEntry(chunk_id="ch_1", graph_id=env.graph_id, position=0)]
    hub.claim_outcome = claimed_outcome("ch_1", env)
    provider = FakeProvider({"e1": "/ws/e1"})
    harness = FakeHarness(handle=_HANDLE, verdict="pass")
    return make_context(store, hub=hub, provider=provider, harness=harness, probe=FakeProbe())


# --- the repository seam directly -------------------------------------------


@pytest.mark.component
def test_record_graph_artifacts_lands_rows_keyed_on_graph_id_in_authored_order(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    store.record_graph_artifacts(
        graph_id="gr_a",
        artifacts=[
            GraphArtifactRecord(name="zulu", ordinal=0, kind=ArtifactKind.ASSET, content="be zulu"),
            GraphArtifactRecord(name="alpha", ordinal=1, kind=ArtifactKind.ASSET, content="be alpha"),
        ],
        recorded_at=_NOW,
    )
    rows = store.graph_artifacts_for_graph("gr_a")
    assert [r.name for r in rows] == ["zulu", "alpha"]
    assert rows[0].kind == ArtifactKind.ASSET
    assert rows[0].content == "be zulu"


@pytest.mark.component
def test_record_graph_artifacts_is_a_no_op_for_an_already_recorded_graph_id(tmp_path):  # type: ignore[no-untyped-def]
    """Insert-if-absent: a second lease against the same mint calls this again with
    the same ``graph_id``. The second call's content deliberately differs from the first's —
    a guard that merely no-ops on identical content would still let this through."""
    store = _store(tmp_path)
    store.record_graph_artifacts(
        graph_id="gr_a",
        artifacts=[GraphArtifactRecord(name="rubric", ordinal=0, kind=ArtifactKind.ASSET, content="first mint")],
        recorded_at=_NOW,
    )
    store.record_graph_artifacts(
        graph_id="gr_a",
        artifacts=[GraphArtifactRecord(name="rubric", ordinal=0, kind=ArtifactKind.ASSET, content="unwritten")],
        recorded_at=_NOW,
    )
    assert [r.content for r in store.graph_artifacts_for_graph("gr_a")] == ["first mint"]


@pytest.mark.component
def test_a_failed_write_lands_no_partial_pin_for_a_later_call_to_freeze(tmp_path):  # type: ignore[no-untyped-def]
    """The insert loop is one transaction: were a partial pin to
    commit, the insert-if-absent guard would see it and freeze the missing rows out forever.
    A repeated ``name`` trips the composite key partway through, standing in for any failure."""
    store = _store(tmp_path)
    with pytest.raises(SQLAlchemyError):
        store.record_graph_artifacts(
            graph_id="gr_a",
            artifacts=[
                GraphArtifactRecord(name="docket", ordinal=0, kind=ArtifactKind.ASSET, content="landed"),
                GraphArtifactRecord(name="docket", ordinal=1, kind=ArtifactKind.ASSET, content="collides"),
            ],
            recorded_at=_NOW,
        )
    assert store.graph_artifacts_for_graph("gr_a") == []

    store.record_graph_artifacts(
        graph_id="gr_a",
        artifacts=[GraphArtifactRecord(name="docket", ordinal=0, kind=ArtifactKind.ASSET, content="retried")],
        recorded_at=_NOW,
    )
    assert [r.content for r in store.graph_artifacts_for_graph("gr_a")] == ["retried"]


@pytest.mark.component
def test_a_superseded_mints_rows_survive_a_later_mints_write(tmp_path):  # type: ignore[no-untyped-def]
    """A re-mint of the same graph name bakes a fresh ``graph_id`` (the hub's own
    immutability guarantee); the runner's mirror is keyed on that id, so the superseded
    mint's rows are untouched by the newer mint's own write."""
    store = _store(tmp_path)
    store.record_graph_artifacts(
        graph_id="gr_old",
        artifacts=[GraphArtifactRecord(name="docket", ordinal=0, kind=ArtifactKind.ASSET, content="old docket")],
        recorded_at=_NOW,
    )
    store.record_graph_artifacts(
        graph_id="gr_new",
        artifacts=[GraphArtifactRecord(name="docket", ordinal=0, kind=ArtifactKind.ASSET, content="new docket")],
        recorded_at=_NOW,
    )
    assert [r.content for r in store.graph_artifacts_for_graph("gr_old")] == ["old docket"]
    assert [r.content for r in store.graph_artifacts_for_graph("gr_new")] == ["new docket"]


# --- Spawner._mint, the real write site --------------------------------------


@pytest.mark.component
def test_fill_pins_the_mints_graph_artifacts_keyed_on_the_envelopes_graph_id(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    env = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        graph_artifacts=[
            GraphArtifact(name="zulu", kind=ArtifactKind.ASSET, content="be zulu"),
            GraphArtifact(name="alpha", kind=ArtifactKind.ASSET, content="be alpha"),
        ],
    )
    Fill(_fill_ctx(store, env)).run()

    rows = store.graph_artifacts_for_graph(env.graph_id)
    assert [r.name for r in rows] == ["zulu", "alpha"]
    assert [r.content for r in rows] == ["be zulu", "be alpha"]


@pytest.mark.component
def test_mint_writes_graph_artifacts_before_recording_the_lease(tmp_path, monkeypatch):  # type: ignore[no-untyped-def]
    """The pin must land BEFORE ``record_lease``, so a crash between the two leaves only
    an orphan row a retry re-writes identically. Swap the order and this reds: at the
    instant ``record_lease`` runs, the pin would not exist yet."""
    store = _store(tmp_path)
    env = make_envelope(
        "ch_1",
        "build",
        node_id="nd_build",
        choices=_CHOICES,
        graph_artifacts=[GraphArtifact(name="docket", kind=ArtifactKind.ASSET, content="be thorough")],
    )
    ctx = _fill_ctx(store, env)

    seen_at_lease_write = []
    original_record_lease = store.record_lease

    def _spy(lease):  # type: ignore[no-untyped-def]
        seen_at_lease_write.append(store.graph_artifacts_for_graph(lease.graph_id))
        return original_record_lease(lease)

    monkeypatch.setattr(store, "record_lease", _spy)
    Fill(ctx).run()

    assert len(seen_at_lease_write) == 1
    assert [a.name for a in seen_at_lease_write[0]] == ["docket"]
