"""The credential-renewal facts against a real runner store (component tier): a claim and its
outcome round-trip as typed fields — an outcome-less claim as unrecorded — the newest claim
per slug wins, and retention keeps each slug's newest claim."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.runner.subscriptions.credential_renewer import RenewalOutcome, RenewalOutcomeKind
from blizzard.runner.usage.repository import CredentialRenewalSummary
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _renew(store: SqlAlchemyRunnerStore, slug: str, at: datetime, outcome: RenewalOutcome | None) -> int:
    claim_id = store.claim_credential_renewal(slug=slug, claimed_at=at)
    if outcome is not None:
        store.record_credential_renewal_outcome(claim_id=claim_id, outcome=outcome, recorded_at=at)
    return claim_id


def test_each_slugs_newest_renewal_round_trips_typed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _renew(store, "renewed", _NOW, RenewalOutcome(RenewalOutcomeKind.RENEWED))
    _renew(store, "failed", _NOW, RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.VENDOR_REFUSED))
    _renew(store, "unrecorded", _NOW, None)

    renewals = store.latest_credential_renewals_by_slug(["renewed", "failed", "unrecorded", "never"])

    assert renewals == {
        "renewed": CredentialRenewalSummary("renewed", _NOW, RenewalResult.RENEWED, None),
        "failed": CredentialRenewalSummary("failed", _NOW, RenewalResult.FAILED, RenewalFailureReason.VENDOR_REFUSED),
        "unrecorded": CredentialRenewalSummary("unrecorded", _NOW, RenewalResult.UNRECORDED, None),
    }
    assert store.latest_credential_renewals_by_slug([]) == {}


def test_the_newest_claim_wins_and_a_same_instant_tie_breaks_on_the_later_claim(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _renew(store, "codex", _NOW - timedelta(hours=1), RenewalOutcome(RenewalOutcomeKind.RENEWED))
    _renew(store, "codex", _NOW, RenewalOutcome(RenewalOutcomeKind.RENEWED))
    _renew(store, "codex", _NOW, RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT))

    summary = store.latest_credential_renewals_by_slug(["codex"])["codex"]

    assert (summary.attempted_at, summary.result) == (_NOW, RenewalResult.FAILED)
    assert store.last_credential_renewal_claim_at("codex") == _NOW
    assert store.last_credential_renewal_claim_at("never") is None


def test_retention_prunes_superseded_claims_with_their_outcomes_but_never_a_slugs_newest(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = _NOW - timedelta(days=3)
    _renew(store, "codex", old, RenewalOutcome(RenewalOutcomeKind.RENEWED))
    _renew(store, "codex", _NOW - timedelta(days=2), RenewalOutcome(RenewalOutcomeKind.RENEWED))
    _renew(store, "stale", old, RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT))

    pruned = store.prune_credential_renewals(now=_NOW)

    assert pruned == 1  # only codex's superseded claim; stale's newest stays regardless of age
    assert store.last_credential_renewal_claim_at("codex") == _NOW - timedelta(days=2)
    renewals = store.latest_credential_renewals_by_slug(["codex", "stale"])
    assert renewals["stale"].result is RenewalResult.FAILED
    with store._engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT count(*) FROM credential_renewal_outcomes").scalar_one() == 2


def test_retention_never_prunes_a_same_instant_tied_newest_pair(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old = _NOW - timedelta(days=3)
    _renew(store, "codex", old, None)
    _renew(store, "codex", old, None)

    assert store.prune_credential_renewals(now=_NOW) == 0
