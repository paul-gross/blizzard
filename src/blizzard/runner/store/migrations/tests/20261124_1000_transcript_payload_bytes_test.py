"""The transcript-payload-bytes revision adds a required ``transcript_outbound_buffer.payload_bytes`` and fills every
existing row, pending or acked, with its payload's byte length across more than one page; its downgrade drops the
column with every row kept, and a second upgrade restores it.

Stores are seeded from SQL written against the parent revision's shape, never from
``src/blizzard/runner/store/schema.py``, whose tables keep changing shape after this revision."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner import runtime as runner_runtime
from tests.support.config_inputs import init_host
from tests.support.runner_scope_seeding import T0

pytestmark = [pytest.mark.component]

_PARENT = "20261120_1000_host_records"

_REVISION = "20261124_1000_transcript_payload_bytes"

_RUNNER_ID = "runner_id-1"

#: More rows than the revision fills in one page.
_PENDING_ROWS = 1203

#: A non-ASCII payload as the buffer stores it: ASCII-escaped JSON, so its length is its byte length.
_ESCAPED = json.dumps({"text": "café ☃"})


def _store_at_parent(tmp_path: Path) -> tuple[MigrationRunner, sa.Engine]:
    """A parent-revision store holding pending rows over several pages, one escaped non-ASCII, and one acked marker."""
    config = init_host(tmp_path)
    migration = runner_runtime.migration_runner(config)
    migration.downgrade(_PARENT)
    engine = create_engine_from_url(config.db_url)
    rows: list[dict[str, object]] = [
        {"seq": seq, "final": False, "payload": json.dumps({"i": seq}), "acked_at": None}
        for seq in range(1, _PENDING_ROWS)
    ]
    rows.append({"seq": _PENDING_ROWS, "final": False, "payload": _ESCAPED, "acked_at": None})
    rows.append(
        {"seq": _PENDING_ROWS + 1, "final": True, "payload": json.dumps({"segment_id": "seg_1"}), "acked_at": T0}
    )
    with engine.begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO transcript_outbound_buffer (seq, segment_id, chunk_id, final, payload, created_at,"
                " acked_at, runner_id) VALUES (:seq, 'seg_1', 'ch_1', :final, :payload, :at, :acked_at, :runner)"
            ),
            [
                {**row, "at": T0.isoformat(), "acked_at": row["acked_at"] and T0.isoformat(), "runner": _RUNNER_ID}
                for row in rows
            ],
        )
    return migration, engine


def _columns(engine: sa.Engine) -> dict[str, tuple[str, bool]]:
    return {
        c["name"]: (str(c["type"]), c["nullable"]) for c in sa.inspect(engine).get_columns("transcript_outbound_buffer")
    }


def _sizes(engine: sa.Engine) -> dict[int, tuple[str, int]]:
    with engine.connect() as conn:
        return {
            seq: (payload, size)
            for seq, payload, size in conn.execute(
                sa.text("SELECT seq, payload, payload_bytes FROM transcript_outbound_buffer")
            )
        }


def test_the_upgrade_fills_every_pending_and_acked_row_with_its_payload_byte_length(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)

        assert _columns(engine)["payload_bytes"] == ("INTEGER", False)
        sizes = _sizes(engine)
        assert len(sizes) == _PENDING_ROWS + 1
        assert all(size == len(payload.encode()) > 0 for payload, size in sizes.values())
        assert sizes[_PENDING_ROWS] == (_ESCAPED, len(_ESCAPED.encode()))
        assert "\\u00e9" in _ESCAPED
    finally:
        engine.dispose()


def test_the_downgrade_drops_the_column_and_a_second_upgrade_restores_it(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        before = _columns(engine)
        migration.upgrade(_REVISION)

        migration.downgrade(_PARENT)

        assert migration.current_revision() == _PARENT
        assert _columns(engine) == before
        with engine.connect() as conn:
            assert conn.execute(sa.text("SELECT count(*) FROM transcript_outbound_buffer")).scalar_one() == (
                _PENDING_ROWS + 1
            )

        migration.upgrade(_REVISION)

        assert _columns(engine)["payload_bytes"] == ("INTEGER", False)
        assert all(size == len(payload.encode()) for payload, size in _sizes(engine).values())
    finally:
        engine.dispose()


def test_a_rerun_of_an_upgrade_that_already_ran_changes_nothing(tmp_path: Path) -> None:
    migration, engine = _store_at_parent(tmp_path)
    try:
        migration.upgrade(_REVISION)
        columns, sizes = _columns(engine), _sizes(engine)
        with engine.begin() as conn:
            conn.execute(sa.text("UPDATE alembic_version SET version_num = :parent"), {"parent": _PARENT})

        migration.upgrade(_REVISION)

        assert migration.current_revision() == _REVISION
        assert (_columns(engine), _sizes(engine)) == (columns, sizes)
    finally:
        engine.dispose()
