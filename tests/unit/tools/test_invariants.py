"""Unit coverage for the facts-level invariant checker (``bzh:invariant-checker``): a release frees only its own runner's binding."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import sqlalchemy as sa

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.store import schema as runner
from blizzard.tools.invariants import runner_invariants
from tests.config_inputs import init_host
from tests.migration_support_invariant_checker import NOW
from tests.runner_fakes import FIXTURE_RUNNER_ID


def _runner_engine(tmp_path: Path) -> sa.Engine:
    """A head-migrated runner store whose identity names the fixture runner, as a registered runner's does."""
    engine = create_engine_from_url(init_host(tmp_path / "runner").db_url)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner.runner_identity).values(runner_id=FIXTURE_RUNNER_ID, runner_name="r", registered_at=NOW)
        )
    return engine


def _unique_env_violations(engine: sa.Engine) -> list[str]:
    return [v.detail for v in runner_invariants(engine).run() if v.invariant == "runner:unique-env-binding"]


_OTHER_RUNNER = "rn_other"


def _two_runner_engine(tmp_path: Path) -> sa.Engine:
    """A store holding two registered runners, each with its own outbox rows 1..3 on both lanes and counters at 3."""
    engine = _runner_engine(tmp_path)
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner.runner_identity).values(runner_id=_OTHER_RUNNER, runner_name="o", registered_at=NOW)
        )
        for runner_id in (FIXTURE_RUNNER_ID, _OTHER_RUNNER):
            for lane, last_seq in ((runner.OUTBOX_LANE_OUTBOUND, 3), (runner.OUTBOX_LANE_TRANSCRIPT, 3)):
                conn.execute(
                    sa.insert(runner.outbox_counters).values(runner_id=runner_id, lane=lane, last_seq=last_seq)
                )
            for seq in (1, 2, 3):
                conn.execute(
                    sa.insert(runner.outbound_buffer).values(
                        runner_id=runner_id, seq=seq, kind="lease.minted", payload="{}", created_at=NOW
                    )
                )
                conn.execute(
                    sa.insert(runner.transcript_outbound_buffer).values(
                        runner_id=runner_id,
                        seq=seq,
                        final=False,
                        segment_id="seg_1",
                        chunk_id="ch_1",
                        payload="{}",
                        created_at=NOW,
                    )
                )
    return engine


def _bind_at(engine: sa.Engine, chunk_id: str, *, runner_id: str, at: datetime, seq: int) -> None:
    """``chunk_id`` bound to ``r1`` of ``ws_a`` by ``runner_id`` at ``at``, the ``seq``-th fact it appended."""
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner.env_bindings).values(
                chunk_id=chunk_id,
                environment_id="r1",
                workdir="/ws_a/r1",
                bound_at=at,
                workspace_id="ws_a",
                runner_id=runner_id,
                seq=seq,
            )
        )


def _release_at(engine: sa.Engine, chunk_id: str, *, runner_id: str, at: datetime, seq: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            sa.insert(runner.binding_releases).values(
                chunk_id=chunk_id, environment_id="r1", released_at=at, runner_id=runner_id, seq=seq
            )
        )


def test_a_release_frees_only_its_own_runners_binding_appended_before_it(tmp_path: Path) -> None:
    """Held means what the runner's own reads mean: no release by the binding's runner appended after the bind."""
    later = NOW + timedelta(hours=1)
    me = FIXTURE_RUNNER_ID

    # Another runner's release of the same (chunk, environment) does not free this runner's binding.
    cross = _two_runner_engine(tmp_path / "cross")
    _bind_at(cross, "ch_1", runner_id=_OTHER_RUNNER, at=NOW, seq=1)
    _release_at(cross, "ch_1", runner_id=me, at=later, seq=1)
    _bind_at(cross, "ch_2", runner_id=me, at=later, seq=2)
    assert len(_unique_env_violations(cross)) == 1

    # A release appended before the bind does not free it, whatever instants they carry.
    early = _runner_engine(tmp_path / "early")
    _release_at(early, "ch_1", runner_id=me, at=later, seq=1)
    _bind_at(early, "ch_1", runner_id=me, at=NOW, seq=2)
    _bind_at(early, "ch_2", runner_id=me, at=later, seq=3)
    assert len(_unique_env_violations(early)) == 1

    # Bind, release, rebind by one runner at one instant: the rebind is held, the released bind is not.
    rebound = _runner_engine(tmp_path / "rebound")
    _bind_at(rebound, "ch_1", runner_id=me, at=NOW, seq=1)
    _release_at(rebound, "ch_1", runner_id=me, at=NOW, seq=2)
    _bind_at(rebound, "ch_1", runner_id=me, at=NOW, seq=3)
    assert _unique_env_violations(rebound) == []
    _bind_at(rebound, "ch_2", runner_id=me, at=later, seq=4)
    assert len(_unique_env_violations(rebound)) == 1

    # A release by the binding's own runner, appended after it, frees it.
    freed = _runner_engine(tmp_path / "freed")
    _bind_at(freed, "ch_1", runner_id=me, at=NOW, seq=1)
    _release_at(freed, "ch_1", runner_id=me, at=NOW, seq=2)
    _bind_at(freed, "ch_2", runner_id=me, at=later, seq=3)
    assert _unique_env_violations(freed) == []
