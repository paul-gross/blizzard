"""The hub client a runner drives its leases through, gated on its identity verdict.

Until the runner has verified that its token resolves to the configured runner, every call
answers :class:`~blizzard.runner.hub.client.HubClientError`, so each step takes the path it already
takes while the hub is unreachable: an unverified runner does only what it does during an outage."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.client import (
    BindingDeclaration,
    ClaimRequest,
    FactPushAck,
    HubClientError,
    HubQuestion,
    IHubClient,
    PushedFact,
    QueueEntry,
    RegistrationReply,
    RouteClaimOutcome,
    SubscriptionDeclaration,
    TranscriptPush,
    TranscriptPushAck,
)
from blizzard.runner.hub.identity import ICurrentIdentityVerdict
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply, Completion, GateSubmission
from blizzard.wire.chunk import HubAdvanceResponse


class IdentityNotVerified(HubClientError):
    """A hub call refused locally: the runner has not verified its token names the configured runner."""


class VerifiedHubClient:
    """An :class:`IHubClient` that forwards to ``inner`` only while ``verdict`` is matched. The
    registration also forwards while the hub has no identity read, its reply then being the check, or refuses the
    runner's version."""

    def __init__(self, inner: IHubClient, verdict: ICurrentIdentityVerdict) -> None:
        self._inner = inner
        self._verdict = verdict

    def _verified(self) -> IHubClient:
        if not self._verdict.matched():
            raise IdentityNotVerified(f"identity {self._verdict.verdict().state}: hub calls wait for a match")
        return self._inner

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkState]:
        return self._verified().chunk_statuses(chunk_ids)

    def peek_queue(
        self,
        capabilities: Sequence[HarnessCapability],
        *,
        policy: str,
        project: str | None = None,
        projects: Sequence[str] | None = None,
    ) -> list[QueueEntry]:
        return self._verified().peek_queue(capabilities, policy=policy, project=project, projects=projects)

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        return self._verified().claim_route(claim)

    def submit_completion(self, chunk_id: str, completion: Completion) -> ApplyReply:
        return self._verified().submit_completion(chunk_id, completion)

    def submit_decision(self, chunk_id: str, gate: GateSubmission) -> ApplyReply:
        return self._verified().submit_decision(chunk_id, gate)

    def push_facts(self, facts: Sequence[PushedFact]) -> FactPushAck:
        return self._verified().push_facts(facts)

    def push_transcripts(self, records: Sequence[TranscriptPush]) -> TranscriptPushAck:
        return self._verified().push_transcripts(records)

    def get_envelope(self, chunk_id: str) -> Envelope:
        return self._verified().get_envelope(chunk_id)

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        return self._verified().hub_advance(chunk_id)

    def get_question(self, question_id: str) -> HubQuestion:
        return self._verified().get_question(question_id)

    def register_runner(
        self,
        name: str,
        workspace_id: str,
        *,
        env_capacity: int | None = None,
        url: str | None = None,
        redirect_uris: tuple[str, ...] = (),
        capabilities: tuple[HarnessCapability, ...] = (),
        subscriptions: tuple[SubscriptionDeclaration, ...] | None = (),
        gates: tuple[str, ...] = (),
        projects: tuple[str, ...] | None = None,
        bindings: tuple[BindingDeclaration, ...] | None = None,
        agents: int | None = None,
        subscription: str | None = None,
    ) -> RegistrationReply:
        retries = self._verdict.registration_checks() or self._verdict.verdict().state is IdentityState.VERSION_REFUSED
        hub = self._inner if retries else self._verified()
        return hub.register_runner(
            name,
            workspace_id,
            env_capacity=env_capacity,
            url=url,
            redirect_uris=redirect_uris,
            capabilities=capabilities,
            subscriptions=subscriptions,
            gates=gates,
            projects=projects,
            bindings=bindings,
            agents=agents,
            subscription=subscription,
        )

    def fetch_runner_paused(self, runner_id: str) -> bool:
        return self._verified().fetch_runner_paused(runner_id)

    def rekey_route_token(self, chunk_id: str) -> str:
        return self._verified().rekey_route_token(chunk_id)


def _conforms_hub_client(x: VerifiedHubClient) -> IHubClient:
    return x
