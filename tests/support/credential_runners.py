"""Seed the runners whose tokens exercise every fleet-credential refusal."""

from typing import NamedTuple, cast

from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from tests.support.scaffolding import HubHarness, seed_runner


class CredentialRunners(NamedTuple):
    live: str
    revoked: str
    retired: str


def seed_credential_runners(hub: HubHarness, *, register_live: bool = True) -> CredentialRunners:
    """Seed ``live``, ``revoked`` (token revoked) and ``retired`` runners; returns their tokens."""
    live = seed_runner(hub, "live", register=register_live)
    revoked = seed_runner(hub, "revoked")
    retired = seed_runner(hub, "retired")
    assert hub.client.post("/api/runners/revoked/token-revocations", json={"by": "operator"}).status_code == 201
    cast(IWriteRunnerRegistry, hub.services.registry).record_lifecycle(
        "retired", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0
    )
    return CredentialRunners(live, revoked, retired)
