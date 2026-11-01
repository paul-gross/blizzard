"""The plural external-usage reads (``bzh:bulk-reconstitution``): each answers, per slug, exactly
what its singular does, and the two callers that fan out over declared subscriptions issue a
statement count that stays flat as the declaration list grows."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.runner.api.host import _subscription_list
from blizzard.runner.config import SubscriptionDeclaration
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC
from tests.runner_fakes import (
    SqlAlchemyRunnerStore,
    make_store,
)
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


def _record(
    store: SqlAlchemyRunnerStore, slug: str, minutes: int, *, payload: str | None, miss_reason: str | None = None
) -> None:
    store.record_external_usage_attempt(
        slug=slug,
        sampled_at=_NOW + timedelta(minutes=minutes),
        payload=payload,
        report_kind="",
        report_payload="",
        miss_reason=miss_reason,
    )


def _seed(store: SqlAlchemyRunnerStore) -> list[str]:
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
    declarations = _declarations(declared)

    count = count_queries(store.engine, lambda: _subscription_list(declarations, store, {}))

    assert count == 3  # one batched read each: the newest attempts, the newest renewals, the newest samples


@pytest.mark.parametrize("declared", [1, 3])
def test_the_sample_at_an_instant_is_one_statement_whatever_the_declared_subscriptions(
    tmp_path: Path, declared: int
) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    for i in range(declared):
        _record(store, f"sub-{i}", 0, payload=_payload(100.0))

    count = count_queries(store.engine, lambda: store.external_usage_sample_at("sub-0", _NOW))

    assert count == 1


def test_the_sample_at_an_instant_reads_only_its_own_slug_and_none_newer(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _record(store, "own", 0, payload=_payload(100.0, resets_in=timedelta(hours=4)))
    _record(store, "own", 10, payload=_payload(20.0, resets_in=timedelta(hours=9)))
    _record(store, "other", 0, payload=_payload(100.0, resets_in=timedelta(hours=1)))

    at = store.external_usage_sample_at("own", _NOW + timedelta(minutes=9))

    assert at is not None
    assert [w.utilization_pct for w in at.windows] == [100.0]


def test_the_sample_at_an_instant_skips_a_miss_and_is_none_before_any_sample(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _record(store, "own", 0, payload=_payload(100.0))
    _record(store, "own", 1, payload=None, miss_reason="credential_lapsed")

    at = store.external_usage_sample_at("own", _NOW + timedelta(minutes=1))

    assert at is not None and at.sampled_at == _NOW
    assert store.external_usage_sample_at("own", _NOW - timedelta(seconds=1)) is None
    assert store.external_usage_sample_at("unsampled", _NOW) is None
