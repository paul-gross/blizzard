"""``GET /api/subscriptions`` (component tier) — every declared
subscription's own newest sampling attempt and credential renewal, reading the store directly
with no fresh sample or renewal of its own."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig, SubscriptionDeclaration
from blizzard.runner.subscriptions.credential_renewer import (
    RenewalFailureReason,
    RenewalOutcome,
    RenewalOutcomeKind,
)
from blizzard.runner.subscriptions.subscription_sampler import PROVIDER_ANTHROPIC, PROVIDER_OPENAI
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, make_stores

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 22, 12, 0, 0, tzinfo=UTC)


def _client(
    tmp_path: Path, *, subscriptions: tuple[SubscriptionDeclaration, ...]
) -> tuple[TestClient, SqlAlchemyRunnerStore]:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}", subscriptions=subscriptions)
    app = create_app(config, runner_stores=make_stores(store))
    return TestClient(app), store


def test_a_never_attempted_slug_reports_every_field_none(tmp_path: Path) -> None:
    client, _store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),),
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "items": [
            {
                "slug": "anthropic",
                "name": "Anthropic",
                "provider": "anthropic",
                "sampled_at": None,
                "ok": None,
                "miss_reason": None,
                "renewal_attempted_at": None,
                "renewal_result": None,
                "renewal_failure_reason": None,
            }
        ]
    }


def test_a_successful_attempt_reports_ok_true_and_no_miss_reason(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),),
    )
    store.record_external_usage_attempt(
        slug="anthropic",
        sampled_at=_NOW,
        payload="{}",
        report_kind="external_subscription_usage.sampled",
        report_payload="{}",
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["sampled_at"] == "2026-09-22T12:00:00+00:00"
    assert item["ok"] is True
    assert item["miss_reason"] is None
    assert item["renewal_result"] is None


def test_a_miss_reports_ok_false_and_its_reason(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),),
    )
    store.record_external_usage_attempt(
        slug="codex",
        sampled_at=_NOW,
        payload=None,
        report_kind="",
        report_payload="",
        miss_reason="credential_lapsed",
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert item["ok"] is False
    assert item["miss_reason"] == "credential_lapsed"


def _renewal_fields(item: dict[str, object]) -> tuple[object, object, object]:
    return item["renewal_attempted_at"], item["renewal_result"], item["renewal_failure_reason"]


def test_a_renewed_outcome_reports_typed_beside_the_attempt(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),),
    )
    claim_id = store.claim_credential_renewal(slug="codex", claimed_at=_NOW)
    store.record_credential_renewal_outcome(
        claim_id=claim_id, outcome=RenewalOutcome(RenewalOutcomeKind.RENEWED), recorded_at=_NOW
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    item = resp.json()["items"][0]
    assert _renewal_fields(item) == ("2026-09-22T12:00:00+00:00", "renewed", None)
    assert item["sampled_at"] is None  # renewal is its own fact, independent of sampling


def test_a_failed_outcome_reports_its_typed_reason(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),),
    )
    claim_id = store.claim_credential_renewal(slug="codex", claimed_at=_NOW)
    store.record_credential_renewal_outcome(
        claim_id=claim_id,
        outcome=RenewalOutcome(RenewalOutcomeKind.FAILED, RenewalFailureReason.TIMED_OUT),
        recorded_at=_NOW,
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    assert _renewal_fields(resp.json()["items"][0]) == ("2026-09-22T12:00:00+00:00", "failed", "timed_out")


def test_a_claim_with_no_outcome_reports_unrecorded(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),),
    )
    store.claim_credential_renewal(slug="codex", claimed_at=_NOW)

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    assert _renewal_fields(resp.json()["items"][0]) == ("2026-09-22T12:00:00+00:00", "unrecorded", None)


def test_several_declared_subscriptions_each_report_their_own_slugs_newest_attempt(tmp_path: Path) -> None:
    client, store = _client(
        tmp_path,
        subscriptions=(
            SubscriptionDeclaration(slug="anthropic", name="Anthropic", provider=PROVIDER_ANTHROPIC),
            SubscriptionDeclaration(slug="codex", name="Codex", provider=PROVIDER_OPENAI),
        ),
    )
    store.record_external_usage_attempt(
        slug="anthropic",
        sampled_at=_NOW,
        payload="{}",
        report_kind="external_subscription_usage.sampled",
        report_payload="{}",
    )
    store.record_external_usage_attempt(
        slug="codex",
        sampled_at=_NOW,
        payload=None,
        report_kind="",
        report_payload="",
        miss_reason="endpoint_unreachable",
    )

    resp = client.get("/api/subscriptions")

    assert resp.status_code == 200, resp.text
    by_slug = {item["slug"]: item for item in resp.json()["items"]}
    assert by_slug["anthropic"]["ok"] is True
    assert by_slug["codex"]["ok"] is False
    assert by_slug["codex"]["miss_reason"] == "endpoint_unreachable"
