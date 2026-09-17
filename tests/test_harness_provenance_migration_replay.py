"""Claude Code resume, judgement, transcript read, and takeover over a backfilled owner —
a runner store is built from the pre-harness-provenance shape,
seeded with an old-shape session (no ``harness_id``), then upgraded to head so the real
migration is what stamps its owner. Every operation below is driven against that
migrated store, never a hand-stamped one, proving the backfilled ``claude_code`` owner
is enough to keep each seam working."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from sqlalchemy import Connection

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.domain.takeover import TakeoverOpenScope, TakeoverService
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.loop.dormant import DormantSession
from blizzard.runner.loop.steps import Advance
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.service import TranscriptService
from blizzard.wire.question import QuestionView
from tests.runner_fakes import (
    FakeArchivedTranscriptRepository,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    make_stores,
    runner_store_errors,
)

pytestmark = pytest.mark.component

_PARENT = "20260913_1100_runner_store_indexes"
_STAMP = "2026-09-11 12:00:00"
_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _migrated_store(tmp_path, seed: Callable[[Connection], None]) -> SqlAlchemyRunnerStore:
    """A store whose schema is built by the real migration chain: ``seed`` writes
    old-shape rows (no ``harness_id``) at the pre-change revision, then the upgrade to
    head runs the actual backfill over them — the owner every test below reads back is
    the migration's own, never asserted by hand."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    migrations.upgrade(_PARENT)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            seed(conn)
    finally:
        engine.dispose()
    migrations.upgrade("head")
    return SqlAlchemyRunnerStore(create_engine_from_url(url), runner_store_errors())


def _seed_lease(
    conn: Connection, *, lease_id: str, chunk_id: str, session_id: str, pid: int, node_id="nd_build", node_name="build"
) -> None:
    """A realistic pre-change Claude Code lease: minted, spawned, session stamped —
    exactly the old shape (no ``harness_id`` anywhere), one node deep."""
    conn.execute(
        sa.text(
            "INSERT INTO leases (lease_id, chunk_id, epoch, runner_id, pid, process_start_time, session_id, created_at) "
            "VALUES (:lease_id, :chunk_id, 1, 'r1', :pid, :start, :session_id, :at)"
        ),
        {
            "lease_id": lease_id,
            "chunk_id": chunk_id,
            "pid": pid,
            "start": f"start-{pid}",
            "session_id": session_id,
            "at": _STAMP,
        },
    )
    conn.execute(
        sa.text(
            "INSERT INTO lease_context (lease_id, chunk_id, graph_id, node_id, node_name, retries_max, recorded_at) "
            "VALUES (:lease_id, :chunk_id, 'gr_1', :node_id, :node_name, 2, :at)"
        ),
        {"lease_id": lease_id, "chunk_id": chunk_id, "node_id": node_id, "node_name": node_name, "at": _STAMP},
    )
    conn.execute(
        sa.text("INSERT INTO lease_spawns (lease_id, spawned_at) VALUES (:lease_id, :at)"),
        {"lease_id": lease_id, "at": _STAMP},
    )


def test_backfilled_session_resumes_on_an_answer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A dormant, ask-parked pre-change session resumes under the backfilled owner: the
    adapter is invoked with the SAME raw session id, and the new spawn generation records
    the adapter's actual version while the migration's own generation stays version-NULL."""

    def seed(conn: Connection) -> None:
        _seed_lease(conn, lease_id="lease_resume", chunk_id="ch_resume", session_id="sess-resume-old", pid=200)
        conn.execute(
            sa.text(
                "INSERT INTO asks (lease_id, chunk_id, question_id, question, options, session_id, asked_at) "
                "VALUES ('lease_resume', 'ch_resume', 'qn_1', 'Which API?', '[]', 'sess-resume-old', :at)"
            ),
            {"at": _STAMP},
        )
        conn.execute(
            sa.text(
                "INSERT INTO park_facts (lease_id, chunk_id, question_id, parked_at) "
                "VALUES ('lease_resume', 'ch_resume', 'qn_1', :at)"
            ),
            {"at": _STAMP},
        )

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_resume", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    lease = store.active_lease("lease_resume")
    assert lease is not None
    assert lease.session == SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-resume-old")

    hub = FakeHub()
    hub.questions["qn_1"] = QuestionView(
        question_id="qn_1",
        chunk_id="ch_resume",
        runner_id="r1",
        epoch=1,
        question="Which API?",
        asked_at="t",
        answered=True,
        answer="rest",
        answered_by="alice",
        answered_at="t2",
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-resume-old", pid=200, process_start_time="start-200", pgid=200), verdict=None
    )
    harness.harness_version = "claude-code/9.9.9"
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    DormantSession(ctx, lease).on_answer()

    # The adapter resumed the exact raw session id, dispatched through the backfilled owner.
    assert harness.resumed == [("/ws/e1", "sess-resume-old", "# Answer from alice. Continue.\nrest")]

    with store._engine.connect() as conn:
        rows = conn.execute(
            sa.text("SELECT harness_id, harness_version FROM lease_spawns WHERE lease_id = 'lease_resume' ORDER BY id")
        ).all()
    # The migration's own backfilled generation: owner known, version never observed.
    assert tuple(rows[0]) == (CLAUDE_CODE_HARNESS_ID, None)
    # This resume's fresh generation: the adapter's actual reported version.
    assert tuple(rows[1]) == (CLAUDE_CODE_HARNESS_ID, "claude-code/9.9.9")


def test_backfilled_session_is_judged_after_exit(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A pre-change session whose worker already exited is judged (launch, then collect)
    under the backfilled owner — the elicitation runs against the same raw session id."""

    def seed(conn: Connection) -> None:
        _seed_lease(conn, lease_id="lease_judge", chunk_id="ch_judge", session_id="sess-judge-old", pid=300)

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_judge", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    hub.envelopes["ch_judge"] = make_envelope("ch_judge", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-judge-old", pid=300, process_start_time="start-300", pgid=300), verdict="pass"
    )
    # pid 300 is not in the probe's live set — the worker already exited.
    ctx = make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=harness,
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
    )

    Advance(ctx).run()  # launch the verdict elicitation

    assert harness.judged and harness.judged[0][1] == "sess-judge-old"

    Advance(ctx).run()  # collect it

    kinds = [b.kind for b in store.pending_outbound()]
    assert "completion.submitted" in kinds


def test_backfilled_session_transcript_reads_from_the_claude_code_source(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The transcript route resolves a backfilled lease's owner through the registry and
    reads its content straight from the ``claude_code`` transcript source."""

    def seed(conn: Connection) -> None:
        _seed_lease(
            conn, lease_id="lease_transcript", chunk_id="ch_transcript", session_id="sess-transcript-old", pid=400
        )

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_transcript", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    turn = NormalizedTurn(
        index=0,
        kind="asst",
        timestamp=_NOW,
        text="hello from the backfilled session",
        tool=None,
        thinking_redacted=False,
        sidechain=None,
        truncated=False,
    )
    source = FakeTranscriptSource(
        batches_by_session={
            "sess-transcript-old": TranscriptBatch(
                session_id="sess-transcript-old",
                available=True,
                reason=None,
                turns=[turn],
                unlinked_sidechains=[],
                next_position=TranscriptPosition("pos-1"),
                complete=True,
                truncated=False,
                sidechain_truncated=False,
                normalizer_version="fake/1",
                harness_version="claude/9",
            )
        }
    )
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-transcript-old", pid=400, process_start_time="start-400", pgid=400),
        verdict=None,
        transcript_source=source,
    )
    registry = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=source)})
    service = TranscriptService(
        leases=store,
        transcript_ledger=store,
        environments=store,
        transcripts=HarnessTranscriptRepositories(registry),
        archived=FakeArchivedTranscriptRepository(),
        workspace_root="",
    )

    resolved = service.for_lease("lease_transcript")

    assert resolved is not None
    assert resolved.transcript.available is True
    assert resolved.transcript.session_id == "sess-transcript-old"
    assert [t.text for t in resolved.transcript.turns] == ["hello from the backfilled session"]


def test_backfilled_session_takeover_opens_under_claude_code(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Opening a takeover over a backfilled session resolves its owner through the
    registry and reports the backfilled ``harness_id`` on the opened command."""

    def seed(conn: Connection) -> None:
        _seed_lease(conn, lease_id="lease_takeover", chunk_id="ch_takeover", session_id="sess-takeover-old", pid=500)

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_takeover", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-takeover-old", pid=500, process_start_time="start-500", pgid=500), verdict=None
    )
    registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    )
    service = TakeoverService(
        make_stores(store),
        FixedClock(_NOW),
        FakeProbe(),
        local_api_url="http://127.0.0.1:8431",
        harnesses=registry,
    )
    scope = TakeoverOpenScope(
        chunk_id="ch_takeover",
        open_takeover=store.open_takeover_for_chunk("ch_takeover"),
        bindings=store.bindings_for_chunk("ch_takeover"),
        active_lease=store.active_lease_for_chunk("ch_takeover"),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk("ch_takeover"),
        latest_epoch=store.latest_epoch("ch_takeover"),
    )

    opened = service.open(scope, force=True)  # the reference lease's worker still reads live

    assert opened.harness_id == CLAUDE_CODE_HARNESS_ID
    assert "sess-takeover-old" in opened.command
