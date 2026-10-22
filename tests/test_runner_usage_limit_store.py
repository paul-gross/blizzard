"""The floor's facts in the store: a subscription's marks and clears on the host, and a runner's own on its bound twin.

Each key's newest mark and clear are what a read answers; a replayed mark (the same invocation) records nothing; a
runner's twin reads and writes only its own rows; and the prune keeps each key's newest past the retention window."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.runner.store.internal.host_usage_limit_store import SubscriptionUsageLimitStore
from blizzard.runner.store.internal.usage_limit_store import RunnerUsageLimitStore
from blizzard.runner.store.runner_guard import UnscopedRunnerStatement
from blizzard.runner.store.schema import runner_usage_limits
from blizzard.runner.store.scoped import RunnerStoreConnections, RunnerUnbound
from blizzard.runner.throttle.usage_limits import FloorClear, UsageLimitMark
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_store_errors

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _mark(at: datetime, *, generation: int = 1, kind: str = "worker", detail: str = "limit reached") -> UsageLimitMark:
    return UsageLimitMark(
        marked_at=at,
        resets_at=at + timedelta(hours=5),
        window="five_hour",
        window_resets_at=at + timedelta(hours=5),
        harness_id="claude-code",
        detail=detail,
        lease_id="lease_1",
        epoch=1,
        generation=generation,
        invocation_kind=kind,  # type: ignore[arg-type]
    )


@pytest.fixture
def store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


@pytest.fixture
def host(store: SqlAlchemyRunnerStore) -> SubscriptionUsageLimitStore:
    return SubscriptionUsageLimitStore(store.connections)


def _twin(store: SqlAlchemyRunnerStore, runner_id: str | None) -> RunnerUsageLimitStore:
    return RunnerUsageLimitStore(RunnerStoreConnections(store._engine, runner_store_errors(), runner_id))


# --- the subscription's marks and clears ----------------------------------------------------------------------------


def test_a_subscription_with_no_facts_reads_none(host: SubscriptionUsageLimitStore) -> None:
    assert host.latest_usage_limit_mark("anthropic") is None
    assert host.latest_floor_clear("anthropic") is None


def test_the_newest_mark_and_clear_per_slug_are_read_whatever_their_insert_order(
    host: SubscriptionUsageLimitStore,
) -> None:
    newer, older = _mark(_T0 + timedelta(minutes=5), generation=2), _mark(_T0, generation=1)
    host.record_usage_limit_mark("anthropic", newer)
    host.record_usage_limit_mark("anthropic", older)
    host.record_usage_limit_mark("openai", _mark(_T0 + timedelta(hours=1), generation=3))
    host.record_floor_clear("anthropic", FloorClear(cleared_at=_T0 + timedelta(minutes=9), by="operator"))
    host.record_floor_clear("anthropic", FloorClear(cleared_at=_T0, by="earlier"))

    assert host.latest_usage_limit_mark("anthropic") == newer
    assert host.latest_floor_clear("anthropic") == FloorClear(cleared_at=_T0 + timedelta(minutes=9), by="operator")
    assert host.latest_floor_clear("openai") is None


def test_a_same_instant_tie_goes_to_the_later_row(host: SubscriptionUsageLimitStore) -> None:
    host.record_usage_limit_mark("anthropic", _mark(_T0, generation=1, detail="first"))
    host.record_usage_limit_mark("anthropic", _mark(_T0, generation=2, detail="second"))
    host.record_floor_clear("anthropic", FloorClear(cleared_at=_T0, by="first"))
    host.record_floor_clear("anthropic", FloorClear(cleared_at=_T0, by="second"))

    mark = host.latest_usage_limit_mark("anthropic")
    assert mark is not None and mark.detail == "second"
    assert host.latest_floor_clear("anthropic") == FloorClear(cleared_at=_T0, by="second")


def test_a_mark_with_no_reset_and_no_held_window_reads_back_as_written(host: SubscriptionUsageLimitStore) -> None:
    bare = UsageLimitMark(
        marked_at=_T0,
        resets_at=None,
        window=None,
        window_resets_at=None,
        harness_id="opencode",
        detail="usage limit",
        lease_id="lease_9",
        epoch=3,
        generation=2,
        invocation_kind="judge",
    )
    assert host.record_usage_limit_mark("anthropic", bare) is True
    assert host.latest_usage_limit_mark("anthropic") == bare


def test_a_replayed_mark_records_nothing(host: SubscriptionUsageLimitStore) -> None:
    assert host.record_usage_limit_mark("anthropic", _mark(_T0)) is True
    # The same invocation again, as a crash before the park replays it — even stamped later.
    assert host.record_usage_limit_mark("anthropic", _mark(_T0 + timedelta(minutes=1))) is False

    assert host.latest_usage_limit_mark("anthropic") == _mark(_T0)


def test_a_judge_invocation_of_the_same_generation_is_its_own_mark(host: SubscriptionUsageLimitStore) -> None:
    assert host.record_usage_limit_mark("anthropic", _mark(_T0, kind="worker")) is True
    assert host.record_usage_limit_mark("anthropic", _mark(_T0 + timedelta(seconds=1), kind="judge")) is True


def test_the_prune_keeps_each_slugs_newest_mark_and_clear_past_the_window(host: SubscriptionUsageLimitStore) -> None:
    now = _T0 + timedelta(days=30)
    old = now - timedelta(days=9)
    recent = now - timedelta(days=7)
    for slug, base in (("anthropic", 0), ("openai", 10)):
        host.record_usage_limit_mark(slug, _mark(old, generation=base + 1))
        host.record_usage_limit_mark(slug, _mark(old + timedelta(minutes=1), generation=base + 2))
        host.record_floor_clear(slug, FloorClear(cleared_at=old, by="a"))
        host.record_floor_clear(slug, FloorClear(cleared_at=old + timedelta(minutes=1), by="b"))
    host.record_usage_limit_mark("anthropic", _mark(recent, generation=99))

    # Per slug: the older mark and clear go; anthropic's aged newer mark goes too, a recent one superseding it.
    assert host.prune_usage_limits(now=now) == 5

    assert host.latest_usage_limit_mark("anthropic") == _mark(recent, generation=99)
    assert host.latest_usage_limit_mark("openai") == _mark(old + timedelta(minutes=1), generation=12)
    assert host.latest_floor_clear("anthropic") == FloorClear(cleared_at=old + timedelta(minutes=1), by="b")
    assert host.prune_usage_limits(now=now) == 0


def test_the_prune_keeps_every_fact_inside_the_window(host: SubscriptionUsageLimitStore) -> None:
    now = _T0 + timedelta(days=30)
    within = now - timedelta(days=7)
    host.record_usage_limit_mark("anthropic", _mark(within, generation=1))
    host.record_usage_limit_mark("anthropic", _mark(within + timedelta(minutes=1), generation=2))

    assert host.prune_usage_limits(now=now) == 0


# --- the runner's own twin ------------------------------------------------------------------------------------------


def test_the_twin_reads_its_newest_mark_and_clear(store: SqlAlchemyRunnerStore) -> None:
    twin = _twin(store, "r1")
    assert twin.latest_usage_limit_mark() is None
    twin.record_usage_limit_mark(_mark(_T0 + timedelta(minutes=5), generation=2))
    twin.record_usage_limit_mark(_mark(_T0, generation=1))
    twin.record_floor_clear(FloorClear(cleared_at=_T0, by="operator"))

    assert twin.latest_usage_limit_mark() == _mark(_T0 + timedelta(minutes=5), generation=2)
    assert twin.latest_floor_clear() == FloorClear(cleared_at=_T0, by="operator")


def test_a_replayed_runner_mark_records_nothing(store: SqlAlchemyRunnerStore) -> None:
    twin = _twin(store, "r1")
    assert twin.record_usage_limit_mark(_mark(_T0)) is True
    assert twin.record_usage_limit_mark(_mark(_T0 + timedelta(minutes=1))) is False


def test_each_runner_reads_writes_and_prunes_only_its_own_floor_facts(store: SqlAlchemyRunnerStore) -> None:
    mine, theirs = _twin(store, "r1"), _twin(store, "r2")
    now = _T0 + timedelta(days=30)
    old = now - timedelta(days=9)
    for twin in (mine, theirs):
        twin.record_usage_limit_mark(_mark(old, generation=1))
        twin.record_usage_limit_mark(_mark(old + timedelta(minutes=1), generation=2))
    # The same invocation is each runner's own, so neither's replay check sees the other's.
    assert theirs.record_usage_limit_mark(_mark(old + timedelta(minutes=2), generation=3)) is True
    mine.record_floor_clear(FloorClear(cleared_at=old, by="mine"))

    assert mine.latest_usage_limit_mark() == _mark(old + timedelta(minutes=1), generation=2)
    assert theirs.latest_floor_clear() is None

    assert mine.prune_usage_limits(now=now) == 1
    assert theirs.latest_usage_limit_mark() == _mark(old + timedelta(minutes=2), generation=3)
    assert theirs.prune_usage_limits(now=now) == 2


def test_an_unbound_twin_refuses_to_write(store: SqlAlchemyRunnerStore) -> None:
    with pytest.raises(RunnerUnbound):
        _twin(store, None).record_floor_clear(FloorClear(cleared_at=_T0, by="operator"))


def test_an_unconfined_read_of_the_runner_table_is_refused(store: SqlAlchemyRunnerStore) -> None:
    with pytest.raises(UnscopedRunnerStatement), store._engine.connect() as conn:
        conn.execute(sa.select(runner_usage_limits))
