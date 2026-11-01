"""Claude Code resume, judgement, transcript read, and takeover over a backfilled owner —
a runner store is built from the pre-harness-provenance shape,
seeded with an old-shape session (no ``harness_id``), then upgraded to head so the real
migration is what stamps its owner. Every operation below is driven against that
migrated store, never a hand-stamped one, proving the backfilled ``claude_code`` owner
is enough to keep each seam working."""

from __future__ import annotations

import shutil
from collections.abc import Callable, Collection
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from click.testing import CliRunner, Result
from sqlalchemy import Connection

from blizzard.cli.main import blizzard
from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.harness.transcript import NormalizedTurn, TranscriptBatch, TranscriptPosition
from blizzard.runner.hub.client import HubQuestion
from blizzard.runner.lifecycle.dormant import DormantSession
from blizzard.runner.lifecycle.takeover import TakeoverOpenScope, TakeoverService
from blizzard.runner.loop.steps import Advance
from blizzard.runner.store import MIGRATIONS_DIR
from blizzard.runner.transcripts.internal.harness_transcript_repositories import HarnessTranscriptRepositories
from blizzard.runner.transcripts.service import TranscriptService
from tests.config_inputs import init_host
from tests.runner_fakes import (
    FIXTURE_RUNNER_ID,
    FakeArchivedTranscriptRepository,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    FakeTranscriptSource,
    SqlAlchemyRunnerStore,
    make_context,
    make_envelope,
    runner_store_errors,
    single_workspace,
)
from tests.runner_scope_seeding import KEYED, T0, reflect, seed_every_keyed_table

pytestmark = [pytest.mark.component]

_PROVENANCE_PARENT = "20260913_1100_runner_store_indexes"

_KEYED_REVISION = "20261011_1000_runner_scope"

_STAMP = "2026-09-11 12:00:00"

_NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

_CHOICES = [("pass", "meets criteria"), ("fail", "does not")]


def _migrated_store(tmp_path: Path, seed: Callable[[Connection], None]) -> SqlAlchemyRunnerStore:
    """A store whose schema is built by the real migration chain: ``seed`` writes
    old-shape rows (no ``harness_id``) at the pre-change revision, then the upgrade to
    head runs the actual backfill over them — the owner every test below reads back is
    the migration's own, never asserted by hand."""
    url = f"sqlite:///{tmp_path / 'runner.db'}"
    migrations = MigrationRunner(script_location=MIGRATIONS_DIR, url=url)
    migrations.upgrade(_PROVENANCE_PARENT)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            seed(conn)
    finally:
        engine.dispose()
    # The runner registers between the releases, so the tightening revision claims the old rows for it.
    migrations.upgrade(_KEYED_REVISION)
    engine = create_engine_from_url(url)
    try:
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (:id, 'r', :at)"),
                {"id": FIXTURE_RUNNER_ID, "at": _STAMP},
            )
    finally:
        engine.dispose()
    migrations.upgrade("head")
    return SqlAlchemyRunnerStore(create_engine_from_url(url), runner_store_errors())


def _seed_lease(
    conn: Connection,
    *,
    lease_id: str,
    chunk_id: str,
    session_id: str,
    pid: int,
    node_id: str = "nd_build",
    node_name: str = "build",
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


def _active_in(store: SqlAlchemyRunnerStore, chunk_id: str, lease_ids: Collection[str]) -> bool:
    active = store.active_lease_for_chunk(chunk_id)
    return active is not None and active.lease_id in lease_ids


_PARENT = "20261009_1000_runner_lease_closures_lease_id_index"

_REVISION = "20261011_1000_runner_scope"

_RUNNER_ID = "rn_01JX0000000000000000000000"

_HOST_TABLES = {"host_identity", "host_outbound_buffer", "host_high_water_seen"}

_ENV_BINDING_COLUMNS = {"workspace_id", "project_id"}

_HEARTBEATS = 2500  # spans several of the backfill's bounded batches


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    return migration, create_engine_from_url(config.db_url)


def _seed_identity(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO runner_identity (runner_id, runner_name, registered_at) VALUES (:id, 'r', :at)"),
            {"id": _RUNNER_ID, "at": T0.isoformat()},
        )


def _runner_ids(engine: sa.Engine) -> dict[str, set[str | None]]:
    with engine.connect() as conn:
        return {name: set(conn.execute(sa.text(f"SELECT DISTINCT runner_id FROM {name}")).scalars()) for name in KEYED}


def _tables(engine: sa.Engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def _row_counts(engine: sa.Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {name: conn.execute(sa.text(f"SELECT count(*) FROM {name}")).scalar_one() for name in _tables(engine)}


def _schema(engine: sa.Engine) -> dict[str, object]:
    inspector = sa.inspect(engine)
    return {
        table: (
            tuple((c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(table)),
            (
                inspector.get_pk_constraint(table)["name"],
                tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
            ),
            frozenset((tuple(i["column_names"]), i["unique"], i["name"]) for i in inspector.get_indexes(table)),
            frozenset((tuple(u["column_names"]), u["name"]) for u in inspector.get_unique_constraints(table)),
            frozenset((c["name"], c["sqltext"]) for c in inspector.get_check_constraints(table)),
            frozenset(
                (tuple(fk["constrained_columns"]), fk["referred_table"], fk["name"])
                for fk in inspector.get_foreign_keys(table)
            ),
        )
        for table in _tables(engine)
    }


def _autoincrement_tables(engine: sa.Engine) -> set[str]:
    """The tables whose DDL names AUTOINCREMENT — which the inspector cannot see."""
    with engine.connect() as conn:
        rows = conn.execute(sa.text("SELECT name, sql FROM sqlite_master WHERE type = 'table'")).all()
    return {name for name, ddl in rows if ddl and "AUTOINCREMENT" in ddl.upper()}


def _copy_with_db_url(original: Path, config_db_url: str, copy: Path) -> None:
    """Copy a host directory for a rehearsal: its host file names no store, so the copy opens its own."""
    shutil.copytree(original, copy)


def _check_invariants(runtime_dir: Path) -> Result:
    return CliRunner().invoke(blizzard, ["dev", "check-invariants", "--runner-dir", str(runtime_dir)])


def _fixture_at_parent(root: Path, *, registered: bool, seeded: bool) -> str:
    """A runner runtime dir whose store the previous release wrote: at the parent revision, with or without an identity,
    and with a row in every runner-scoped table or none. Each table is shaped as live, an open transcript segment
    included; answers the dir's store url."""
    config = init_host(root)
    runner_runtime.migration_runner(config).downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    try:
        if registered:
            _seed_identity(engine)
        if seeded:
            seed_every_keyed_table(engine, reflect(engine), omit=frozenset({"finalized_at"}))
    finally:
        engine.dispose()
    return config.db_url


def test_backfilled_session_resumes_on_an_answer(tmp_path: Path) -> None:
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
    hub.questions["qn_1"] = HubQuestion(
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
        handle=WorkerHandle(session_id="sess-resume-old", pid=200, process_start_time="start-200", pgid=200),
        verdict=None,
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

    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                sa.text(
                    "SELECT harness_id, harness_version FROM lease_spawns WHERE lease_id = 'lease_resume' ORDER BY id"
                )
            ).all()
    finally:
        engine.dispose()
    # The migration's own backfilled generation: owner known, version never observed.
    assert tuple(rows[0]) == (CLAUDE_CODE_HARNESS_ID, None)
    # This resume's fresh generation: the adapter's actual reported version.
    assert tuple(rows[1]) == (CLAUDE_CODE_HARNESS_ID, "claude-code/9.9.9")


def test_backfilled_session_is_judged_after_exit(tmp_path: Path) -> None:
    """A pre-change session whose worker already exited is judged (launch, then collect)
    under the backfilled owner — the elicitation runs against the same raw session id."""

    def seed(conn: Connection) -> None:
        _seed_lease(conn, lease_id="lease_judge", chunk_id="ch_judge", session_id="sess-judge-old", pid=300)

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_judge", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    hub = FakeHub()
    hub.envelopes["ch_judge"] = make_envelope("ch_judge", "build", node_id="nd_build", choices=_CHOICES)
    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-judge-old", pid=300, process_start_time="start-300", pgid=300),
        verdict="pass",
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


def test_backfilled_session_transcript_reads_from_the_claude_code_source(tmp_path: Path) -> None:
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
        workspaces=single_workspace(FakeProvider({})),
    )

    resolved = service.for_lease("lease_transcript")

    assert resolved is not None
    assert resolved.transcript.available is True
    assert resolved.transcript.session_id == "sess-transcript-old"
    assert [t.text for t in resolved.transcript.turns] == ["hello from the backfilled session"]


def test_backfilled_session_takeover_opens_under_claude_code(tmp_path: Path) -> None:
    """Opening a takeover over a backfilled session resolves its owner through the
    registry and reports the backfilled ``harness_id`` on the opened command."""

    def seed(conn: Connection) -> None:
        _seed_lease(conn, lease_id="lease_takeover", chunk_id="ch_takeover", session_id="sess-takeover-old", pid=500)

    store = _migrated_store(tmp_path, seed)
    store.record_binding(chunk_id="ch_takeover", environment_id="e1", workdir="/ws/e1", bound_at=_NOW)

    harness = FakeHarness(
        handle=WorkerHandle(session_id="sess-takeover-old", pid=500, process_start_time="start-500", pgid=500),
        verdict=None,
    )
    registry = HarnessRegistry(
        {CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=harness, transcript_source=harness.transcript_source())}
    )
    service = TakeoverService(
        FixedClock(_NOW),
        FakeProbe(),
        takeover=store,
        outbound=store,
        tokens=store,
        elicitations=store,
        local_api_url="http://127.0.0.1:8431",
        workspaces=single_workspace(FakeProvider({})),
        harnesses=registry,
    )
    scope = TakeoverOpenScope(
        chunk_id="ch_takeover",
        open_takeover=store.open_takeover_for_chunk("ch_takeover"),
        bindings=store.bindings_for_chunk("ch_takeover"),
        active_lease=store.active_lease_for_chunk("ch_takeover"),
        latest_lease_with_session=store.latest_lease_with_session_for_chunk("ch_takeover"),
        latest_epoch=store.latest_epoch("ch_takeover"),
        active_parked=_active_in(store, "ch_takeover", store.parked_lease_ids()),
        submission_pending=_active_in(store, "ch_takeover", store.pending_submission_lease_ids()),
    )

    opened = service.open(scope, force=True)  # the reference lease's worker still reads live

    assert opened.harness_id == CLAUDE_CODE_HARNESS_ID
    assert "sess-takeover-old" in opened.command


def test_the_revision_keys_exactly_the_runner_scoped_tables(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before = _tables(engine)
        migration.upgrade(_REVISION)

        assert _tables(engine) - before == _HOST_TABLES
        inspector = sa.inspect(engine)
        keyed = {t for t in _tables(engine) if "runner_id" in {c["name"] for c in inspector.get_columns(t)}}
        assert keyed == KEYED | {"runner_identity"}
        for table in KEYED:
            [runner_id] = [c for c in inspector.get_columns(table) if c["name"] == "runner_id"]
            assert runner_id["nullable"], table
        added = {c["name"] for c in inspector.get_columns("env_bindings")} & _ENV_BINDING_COLUMNS
        assert added == _ENV_BINDING_COLUMNS
        assert all(c["nullable"] for c in inspector.get_columns("env_bindings") if c["name"] in _ENV_BINDING_COLUMNS)
    finally:
        engine.dispose()


def test_upgrade_fills_every_runner_scoped_row_from_the_identity(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        seed_every_keyed_table(engine, reflect(engine))
        with engine.begin() as conn:
            conn.execute(
                sa.text("INSERT INTO heartbeats (lease_id, beat_at) VALUES ('lease-1', :at)"),
                [{"at": T0.isoformat()} for _ in range(_HEARTBEATS)],
            )
        counts = _row_counts(engine)

        migration.upgrade(_REVISION)

        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in KEYED}
        assert {t: n for t, n in _row_counts(engine).items() if t in counts} == counts
        with engine.connect() as conn:
            stamped = conn.execute(sa.text("SELECT count(*) FROM heartbeats WHERE runner_id = :id"), {"id": _RUNNER_ID})
            assert stamped.scalar_one() == _HEARTBEATS + 1
    finally:
        engine.dispose()


def test_a_store_with_no_identity_keeps_every_runner_id_null(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        seed_every_keyed_table(engine, reflect(engine))

        migration.upgrade(_REVISION)

        assert _runner_ids(engine) == {table: {None} for table in KEYED}
    finally:
        engine.dispose()


def test_a_rerun_after_an_interrupted_upgrade_finishes_it(tmp_path: Path) -> None:
    """Re-entry: some tables already carry the column, some rows are already stamped, and one host table already
    exists — the upgrade skips what is done and completes the rest."""
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        seed_every_keyed_table(engine, reflect(engine))
        done = sorted(KEYED)[: len(KEYED) // 2]
        with engine.begin() as conn:
            for table in done:
                conn.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN runner_id VARCHAR"))
            conn.execute(sa.text(f"UPDATE {done[0]} SET runner_id = :id"), {"id": _RUNNER_ID})
            conn.execute(sa.text("ALTER TABLE env_bindings ADD COLUMN workspace_id VARCHAR"))
            conn.execute(
                sa.text(
                    "CREATE TABLE host_identity (id INTEGER NOT NULL PRIMARY KEY, host_id VARCHAR NOT NULL,"
                    " tenant_id VARCHAR, registered_at DATETIME NOT NULL)"
                )
            )

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in KEYED}
        assert _tables(engine) >= _HOST_TABLES
        assert {c["name"] for c in sa.inspect(engine).get_columns("env_bindings")} >= _ENV_BINDING_COLUMNS
    finally:
        engine.dispose()


def test_downgrade_restores_the_parent_schema_exactly(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        seed_every_keyed_table(engine, reflect(engine))
        schema_before, counts_before = _schema(engine), _row_counts(engine)

        migration.upgrade(_REVISION)
        migration.downgrade(_PARENT)

        assert _schema(engine) == schema_before
        assert _row_counts(engine) == counts_before
    finally:
        engine.dispose()


def test_a_downgrade_and_upgrade_keep_every_autoincrement_table_autoincrementing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        declared = _autoincrement_tables(engine)
        assert declared

        migration.upgrade(_REVISION)
        assert _autoincrement_tables(engine) >= declared
        migration.downgrade(_PARENT)
        assert _autoincrement_tables(engine) == declared
        migration.upgrade(_REVISION)
        assert _autoincrement_tables(engine) >= declared
    finally:
        engine.dispose()


def test_the_previous_releases_statements_write_to_the_migrated_store_with_no_runner(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        _seed_identity(engine)
        parent_shapes = reflect(engine)
        migration.upgrade(_REVISION)

        seed_every_keyed_table(engine, parent_shapes)

        assert _runner_ids(engine) == {table: {None} for table in KEYED}
    finally:
        engine.dispose()


def test_the_cli_migrates_a_copied_store_down_to_the_parent_and_back(tmp_path: Path) -> None:
    original = tmp_path / "original"
    config = init_host(original)
    runner_runtime.migration_runner(config).downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    try:
        _seed_identity(engine)
        seed_every_keyed_table(engine, reflect(engine))
        parent_schema = _schema(engine)
    finally:
        engine.dispose()
    copy = tmp_path / "copy"
    shutil.copytree(original, copy)
    # The host file names no store, so the copy opens its own.
    copy_url = f"sqlite:///{copy / 'data' / 'host.db'}"
    cli = CliRunner()

    def migrate(*args: str) -> None:
        result = cli.invoke(blizzard, ["runner", "migrate", "--dir", str(copy), *args])
        assert result.exit_code == 0, result.output

    engine = create_engine_from_url(copy_url)
    try:
        migrate()
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in KEYED}

        migrate("--down", _PARENT)
        assert _schema(engine) == parent_schema

        migrate()
        assert _runner_ids(engine) == {table: {_RUNNER_ID} for table in KEYED}
        assert _tables(engine) >= _HOST_TABLES
    finally:
        engine.dispose()
    original_engine = create_engine_from_url(config.db_url)
    try:
        assert _schema(original_engine) == parent_schema
    finally:
        original_engine.dispose()


@pytest.mark.parametrize(
    ("registered", "seeded"), [(True, True), (True, False), (False, False)], ids=["registered", "idle", "fresh"]
)
def test_a_migrated_copy_of_a_previous_release_store_holds_every_invariant(
    tmp_path: Path, registered: bool, seeded: bool
) -> None:
    original = tmp_path / "original"
    db_url = _fixture_at_parent(original, registered=registered, seeded=seeded)
    copy = tmp_path / "copy"
    _copy_with_db_url(original, db_url, copy)

    migrated = CliRunner().invoke(blizzard, ["runner", "migrate", "--dir", str(copy)])
    assert migrated.exit_code == 0, migrated.output
    checked = _check_invariants(copy)
    assert checked.exit_code == 0, checked.output
    assert "invariants hold" in checked.output
