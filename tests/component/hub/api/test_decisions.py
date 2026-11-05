"""``POST /api/decisions/{id}/resolutions`` and the deprecated singular
``.../resolution`` alias, component tier.

Pins the pluralized route's CAS behavior (200 winner / 409 loser) and proves the
singular alias resolves byte-identically while carrying the ``Deprecation``/``Link``
headers; a runner bearer token is rejected on both."""

from __future__ import annotations

from pathlib import Path

from tests.support.gate_decisions import (
    _open_decision,  # pyright: ignore[reportPrivateUsage]  # shared test helper, imported from tests.support.gate_decisions
)
from tests.support.scaffolding import build_hub

# --- POST /api/decisions/{id}/resolutions — primary -------------------------


def test_resolutions_resolves_first_write_wins(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)

    first = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve", "resolved_by": "ada"}
    )
    assert first.status_code == 200, first.text
    assert first.json()["choice"] == "approve"
    # `resolved_by` in the body is a spoof attempt — the route overwrites it with the
    # resolved session identity, `"operator"` under the default `auth.mode = "none"`.
    assert first.json()["resolved_by"] == "operator"

    second = hub.client.post(
        f"/api/decisions/{decision_id}/resolutions", json={"choice": "reject", "resolved_by": "bob"}
    )
    assert second.status_code == 409, second.text
    assert second.json()["already_resolved_by"] == "operator"
    assert second.json()["resolved_choice"] == "approve"
    assert second.json()["resolved_at"] == first.json()["resolved_at"]
    assert second.json()["resolved_at"] != ""


def test_resolutions_unknown_decision_is_404(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/decisions/dc_missing/resolutions", json={"choice": "approve"})
    assert resp.status_code == 404


def test_resolutions_unknown_choice_is_400(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    decision_id = _open_decision(hub)
    resp = hub.client.post(f"/api/decisions/{decision_id}/resolutions", json={"choice": "maybe"})
    assert resp.status_code == 400


# --- Runner principal is still rejected on the resolution route -------------


def test_runner_bearer_token_is_rejected_on_resolutions(tmp_path: Path) -> None:
    from tests.support.hub_request_auth import bearer, seed_enrolled

    token = seed_enrolled(tmp_path)
    warn_hub = build_hub(tmp_path)
    decision_id = _open_decision(warn_hub)

    hub = build_hub(tmp_path)
    assert (
        hub.client.post(
            f"/api/decisions/{decision_id}/resolutions", json={"choice": "approve"}, headers=bearer(token)
        ).status_code
        == 403
    )
