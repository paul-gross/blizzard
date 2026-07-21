"""The store-backed jti replay cache — single-txn insert under the ``jti`` primary key
gives the single-use guarantee outright (issue #95, decision D4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.auth.internal.jti_cache_repository import JtiCacheRepository
from blizzard.runner.store.schema import jwt_jti_seen, metadata

pytestmark = pytest.mark.unit


def _repository(tmp_path: Path) -> JtiCacheRepository:
    return JtiCacheRepository(_engine(tmp_path))


def _engine(tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'runner.db'}")
    metadata.create_all(engine)
    return engine


def test_first_presentation_is_admitted(tmp_path: Path) -> None:
    cache = _repository(tmp_path)
    admitted = cache.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    assert admitted is True


def test_a_replayed_jti_is_refused(tmp_path: Path) -> None:
    cache = _repository(tmp_path)
    cache.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    replayed = cache.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC))
    assert replayed is False


def test_a_replay_from_a_second_process_over_the_same_store_is_still_refused(tmp_path: Path) -> None:
    """Survives a restart within the token's window (D4's own point): two independent
    repository instances over the same on-disk store still share the single-use
    guarantee — it lives in the store, not in either process's memory."""
    first_process = _repository(tmp_path)
    second_process = _repository(tmp_path)
    assert first_process.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC)) is True
    assert (
        second_process.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC)) is False
    )


def test_distinct_jtis_are_independently_admitted(tmp_path: Path) -> None:
    cache = _repository(tmp_path)
    assert cache.check_and_record("jti-1", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC)) is True
    assert cache.check_and_record("jti-2", aud="runner-a", expires_at=datetime(2099, 1, 1, tzinfo=UTC)) is True


def test_an_expired_row_is_opportunistically_pruned_on_the_next_insert(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cache = JtiCacheRepository(engine)
    past = datetime.now(UTC) - timedelta(hours=1)
    cache.check_and_record("jti-old", aud="runner-a", expires_at=past)

    cache.check_and_record("jti-new", aud="runner-a", expires_at=datetime.now(UTC) + timedelta(minutes=1))

    with engine.connect() as conn:
        remaining = {row.jti for row in conn.execute(select(jwt_jti_seen.c.jti))}
    assert remaining == {"jti-new"}


def test_pruning_never_admits_a_replayed_still_live_jti(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    cache = JtiCacheRepository(engine)
    live = datetime.now(UTC) + timedelta(minutes=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    assert cache.check_and_record("jti-live", aud="runner-a", expires_at=live) is True

    # An unrelated expired row's prune must not disturb the still-live jti's PK guard.
    cache.check_and_record("jti-old", aud="runner-a", expires_at=past)
    replayed = cache.check_and_record("jti-live", aud="runner-a", expires_at=live)
    assert replayed is False
