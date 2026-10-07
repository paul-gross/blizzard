"""``CredentialRenewalPass`` — renewal off the tick, claimed before it fires (unit tier, real
sqlite store, scriptable ``FakeCredentialRenewer``): the claim/outcome write order, its
failure cases, and that a renewal blocked on its own driver never delays a concurrent tick."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.logging import get_logger
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.loop.context import LoopConfig
from blizzard.runner.loop.tick import tick
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome, RenewalOutcomeKind
from blizzard.runner.usage.credential_renewal import CredentialRenewalPass, RenewableSubscription
from blizzard.runner.usage.periodic_pass_driver import PeriodicPassDriver
from tests.runner_fakes import (
    FakeCredentialRenewer,
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    SqlAlchemyRunnerStore,
    make_context,
    make_store,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
_SLUG = "codex"
_INTERVAL = 300


def _store(tmp_path) -> SqlAlchemyRunnerStore:  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _pass(
    store: object, renewer: FakeCredentialRenewer, clock: FixedClock, *, slug: str = _SLUG
) -> CredentialRenewalPass:
    return CredentialRenewalPass(
        subscriptions=(RenewableSubscription(slug=slug, sample_interval_seconds=_INTERVAL, renewer=renewer),),
        renewals=store,  # type: ignore[arg-type]
        clock=clock,
    )


class _FailingWrites:
    """The real store, except the named write raises — the commit that fails mid-pass."""

    def __init__(self, store: SqlAlchemyRunnerStore, *, failing: str) -> None:
        self._store = store
        self._failing = failing

    def __getattr__(self, name: str) -> object:
        if name == self._failing:

            def _raise(**_: object) -> None:
                raise RuntimeError(f"{name} failed")

            return _raise
        return getattr(self._store, name)


def test_a_due_renewal_is_claimed_then_renewed_and_its_outcome_recorded(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    claims_seen_by_renew: list[datetime | None] = []
    renewer = FakeCredentialRenewer(
        on_renew=lambda: claims_seen_by_renew.append(store.last_credential_renewal_claim_at(_SLUG))
    )

    _pass(store, renewer, FixedClock(_NOW)).run()

    assert claims_seen_by_renew == [_NOW]  # the claim was committed before the renewal fired
    summary = store.latest_credential_renewals_by_slug([_SLUG])[_SLUG]
    assert (summary.attempted_at, summary.result, summary.failure_reason) == (_NOW, RenewalResult.RENEWED, None)


def test_a_failed_renewal_records_its_typed_reason(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    renewer = FakeCredentialRenewer(outcome=RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT))

    _pass(store, renewer, FixedClock(_NOW)).run()

    summary = store.latest_credential_renewals_by_slug([_SLUG])[_SLUG]
    assert (summary.result, summary.failure_reason) == (RenewalResult.FAILED, RenewalFailureReason.TIMED_OUT)


def test_a_not_due_check_writes_nothing_and_never_renews(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    renewer = FakeCredentialRenewer(due=False)

    _pass(store, renewer, FixedClock(_NOW)).run()

    assert renewer.due_calls == 1
    assert renewer.renew_calls == 0
    assert store.last_credential_renewal_claim_at(_SLUG) is None
    assert store.latest_credential_renewals_by_slug([_SLUG]) == {}


def test_a_failed_claim_write_never_calls_the_renewer(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    renewer = FakeCredentialRenewer()

    _pass(_FailingWrites(store, failing="claim_credential_renewal"), renewer, FixedClock(_NOW)).run()  # must not raise

    assert renewer.renew_calls == 0
    assert store.last_credential_renewal_claim_at(_SLUG) is None


def test_a_failed_outcome_write_leaves_the_claim_standing_so_no_renewal_repeats_inside_the_cadence(
    tmp_path,  # type: ignore[no-untyped-def]
) -> None:
    store = _store(tmp_path)
    renewer = FakeCredentialRenewer()
    clock = FixedClock(_NOW)
    renewal_pass = _pass(_FailingWrites(store, failing="record_credential_renewal_outcome"), renewer, clock)

    renewal_pass.run()  # must not raise
    assert renewer.renew_calls == 1

    clock.advance(timedelta(seconds=_INTERVAL - 1))
    renewal_pass.run()

    assert renewer.renew_calls == 1  # the standing claim anchors the cadence
    summary = store.latest_credential_renewals_by_slug([_SLUG])[_SLUG]
    assert (summary.attempted_at, summary.result, summary.failure_reason) == (
        _NOW,
        RenewalResult.UNRECORDED,
        None,
    )


def test_a_renewal_repeats_only_on_the_slugs_own_cadence(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    renewer = FakeCredentialRenewer()
    clock = FixedClock(_NOW)
    renewal_pass = _pass(store, renewer, clock)

    renewal_pass.run()
    clock.advance(timedelta(seconds=_INTERVAL - 1))
    renewal_pass.run()
    assert (renewer.due_calls, renewer.renew_calls) == (1, 1)  # gated before the due check

    clock.advance(timedelta(seconds=1))
    renewal_pass.run()
    assert renewer.renew_calls == 2


def test_one_slugs_failure_never_skips_the_next(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)

    def _boom() -> None:
        raise RuntimeError("renewer broke its contract")

    broken = FakeCredentialRenewer(on_renew=_boom)
    healthy = FakeCredentialRenewer()
    renewal_pass = CredentialRenewalPass(
        subscriptions=(
            RenewableSubscription(slug="broken", sample_interval_seconds=_INTERVAL, renewer=broken),
            RenewableSubscription(slug=_SLUG, sample_interval_seconds=_INTERVAL, renewer=healthy),
        ),
        renewals=store,
        clock=FixedClock(_NOW),
    )

    renewal_pass.run()  # must not raise

    renewals = store.latest_credential_renewals_by_slug(["broken", _SLUG])
    assert renewals["broken"].result is RenewalResult.UNRECORDED
    assert renewals[_SLUG].result is RenewalResult.RENEWED


def test_a_renewal_blocked_on_its_driver_never_delays_a_concurrent_tick(tmp_path) -> None:  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def _block() -> None:
        entered.set()
        release.wait(10)

    renewer = FakeCredentialRenewer(on_renew=_block)
    driver = PeriodicPassDriver(
        _pass(store, renewer, FixedClock(_NOW)).run,
        name="test-credential-renewal",
        label="credential renewal pass",
        log=get_logger("tests.credential_renewal"),
        interval_seconds=60,
        jitter_seconds=0,
        stop_timeout_seconds=0.2,
    )
    ctx = make_context(
        store,
        hub=FakeHub(),
        provider=FakeProvider({}),
        harness=FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict="pass"),
        probe=FakeProbe(),
        clock=FixedClock(_NOW),
        config=LoopConfig(runner_name="r1", workspace_id="ws1", max_agents=1),
    )
    driver.start()
    try:
        assert entered.wait(5)  # the renewal is now blocked inside the vendor call

        started = time.monotonic()
        tick(ctx)
        assert time.monotonic() - started < 5

        stop_started = time.monotonic()
        driver.stop()
        assert time.monotonic() - stop_started < 2
    finally:
        release.set()
