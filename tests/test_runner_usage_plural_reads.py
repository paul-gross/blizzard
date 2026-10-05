"""The plural external-usage reads (``bzh:bulk-reconstitution``): each answers, per slug, exactly
what its singular does, and the two callers that fan out over declared subscriptions issue a
statement count that stays flat as the declaration list grows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.runner.api.subscriptions import _subscription_list
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.lifecycle.usage_limit import _fallback_reset
from blizzard.runner.loop.context import LoopConfig, ResolvedSubscription
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.runner_fakes import FakeHarness, FakeHub, FakeProbe, FakeProvider, make_context, make_store
from tests.support import count_queries

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _payload(utilization: float, *, resets_in: timedelta = timedelta(hours=2)) -> str:
    return json.dumps(
        {
            "windows": [
                {
                    "window": "5h",
                    "utilization_pct": utilization,
                    "resets_at": (_NOW + resets_in).isoformat(),
                    "window_seconds": 18_000,
                }
            ]
        }
    )


def _record(store, slug: str, minutes: int, *, payload: str | None, miss_reason: str | None = None) -> None:  # type: ignore[no-untyped-def]
    store.record_external_usage_attempt(
        slug=slug,
        sampled_at=_NOW + timedelta(minutes=minutes),
        payload=payload,
        report_kind="",
        report_payload="",
        miss_reason=miss_reason,
    )


def _seed(store) -> list[str]:  # type: ignore[no-untyped-def]
    """never-sampled, ok, miss, and a newest miss hiding an older full window."""
    _record(store, "ok", 0, payload=_payload(40.0))
    _record(store, "ok", 1, payload=_payload(55.0))
    _record(store, "miss", 0, payload=None, miss_reason="unreachable")
    _record(store, "hidden", 0, payload=_payload(100.0))
    _record(store, "hidden", 1, payload=None, miss_reason="unreachable")
    return ["never", "ok", "miss", "hidden"]


def test_the_plurals_answer_each_slug_exactly_as_the_singulars_do(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    slugs = _seed(store)

    windows = store.latest_external_usage_windows_by_slug(slugs)
    attempts = store.latest_external_usage_attempts_by_slug(slugs)

    for slug in slugs:
        assert windows.get(slug, ()) == store.latest_external_usage_windows(slug)
        assert attempts.get(slug) == store.latest_external_usage_attempt(slug)
    assert set(windows) == {"ok", "hidden"}
    assert set(attempts) == {"ok", "miss", "hidden"}


def test_a_same_instant_tie_resolves_to_the_later_recorded_row_as_the_singulars_do(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    # Both slugs record two rows at one instant; "b" interleaves so row order is not slug order.
    _record(store, "a", 0, payload=_payload(30.0))
    _record(store, "b", 0, payload=_payload(60.0))
    _record(store, "a", 0, payload=_payload(70.0))
    _record(store, "b", 0, payload=None, miss_reason="unreachable")

    windows = store.latest_external_usage_windows_by_slug(["a", "b"])
    attempts = store.latest_external_usage_attempts_by_slug(["a", "b"])

    assert [w.utilization_pct for w in windows["a"]] == [70.0]
    # The newer same-instant miss hides nothing from the windows read, which skips misses.
    assert [w.utilization_pct for w in windows["b"]] == [60.0]
    assert attempts["b"].miss_reason == "unreachable"
    for slug in ("a", "b"):
        assert windows[slug] == store.latest_external_usage_windows(slug)
        assert attempts[slug] == store.latest_external_usage_attempt(slug)


def test_an_older_row_recorded_later_never_reads_as_its_slugs_newest(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    # "x"'s older row carries the higher id, and shares its instant with "y"'s newest.
    _record(store, "x", 1, payload=_payload(20.0))
    _record(store, "x", 0, payload=_payload(80.0))
    _record(store, "y", 0, payload=_payload(50.0))

    windows = store.latest_external_usage_windows_by_slug(["x", "y"])

    assert [w.utilization_pct for w in windows["x"]] == [20.0]
    assert [w.utilization_pct for w in windows["y"]] == [50.0]
    assert windows["x"] == store.latest_external_usage_windows("x")


def test_the_plurals_of_no_slugs_are_empty(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")

    assert store.latest_external_usage_windows_by_slug([]) == {}
    assert store.latest_external_usage_attempts_by_slug([]) == {}


def _declarations(n: int) -> tuple[SubscriptionDeclaration, ...]:
    return tuple(
        SubscriptionDeclaration(slug=f"sub-{i}", name=f"Sub {i}", provider=PROVIDER_ANTHROPIC) for i in range(n)
    )


@pytest.mark.parametrize("declared", [1, 3])
def test_subscription_list_statement_count_is_flat_in_the_declared_subscriptions(tmp_path: Path, declared: int) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for i in range(declared):
        _record(store, f"sub-{i}", 0, payload=_payload(10.0))
    config = RunnerConfig(
        root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", subscriptions=_declarations(declared)
    )

    count = count_queries(store._engine, lambda: _subscription_list(config, store))

    assert count == 2  # one batched read each: the newest attempts, the newest renewals


@pytest.mark.parametrize("declared", [1, 3])
def test_fallback_reset_statement_count_is_flat_in_the_declared_subscriptions(tmp_path: Path, declared: int) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for i in range(declared):
        _record(store, f"sub-{i}", 0, payload=_payload(100.0))
    resolved = tuple(
        ResolvedSubscription(
            slug=f"sub-{i}",
            name=f"Sub {i}",
            provider=PROVIDER_ANTHROPIC,
            sample_interval_seconds=300,
            sampler=None,
        )
        for i in range(declared)
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
        subscriptions=resolved,
    )

    count = count_queries(store._engine, lambda: _fallback_reset(ctx))

    assert count == 1
    assert _fallback_reset(ctx) == _NOW + timedelta(hours=2)


def test_fallback_reset_picks_the_soonest_reset_across_declared_slugs(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _record(store, "late", 0, payload=_payload(100.0, resets_in=timedelta(hours=4)))
    _record(store, "soon", 0, payload=_payload(100.0, resets_in=timedelta(hours=1)))
    _record(store, "partial", 0, payload=_payload(90.0, resets_in=timedelta(minutes=10)))
    # Recorded, but not declared: its sooner reset is never consulted.
    _record(store, "undeclared", 0, payload=_payload(100.0, resets_in=timedelta(minutes=5)))
    resolved = tuple(
        ResolvedSubscription(
            slug=slug, name=slug, provider=PROVIDER_ANTHROPIC, sample_interval_seconds=300, sampler=None
        )
        for slug in ("late", "soon", "partial")
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_id="r1", workspace_id="ws1", max_agents=1),
        subscriptions=resolved,
    )

    assert _fallback_reset(ctx) == _NOW + timedelta(hours=1)
