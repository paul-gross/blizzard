"""The hub-client seam — the runner's outbound-only edge to the hub HTTP API.

Every fleet call presents the runner's own bearer token, and the hub takes the runner's identity
from that token alone. The calls made under an operator's credential instead — adding a runner or a
host — have their own narrow port, :class:`IHubRunnerAdmin`, and the calls a host makes under its own
token have theirs, :class:`IHostHubClient`."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.runner_version import VersionRefusalReason
from blizzard.runner.harness.capability_snapshot import HarnessCapability
from blizzard.runner.hub.served_projects import ServedProject
from blizzard.runner.node_steps.chunk_state import ChunkState
from blizzard.runner.node_steps.envelope import Envelope
from blizzard.runner.node_steps.submissions import ApplyReply, Completion, GateSubmission
from blizzard.wire.chunk import HubAdvanceResponse


class HubClientError(RuntimeError):
    """A hub call failed at the transport level (unreachable, 5xx, malformed body).

    A 409 route conflict and a 403 paused denial are **not** errors — they are expected
    claim outcomes returned as :class:`RouteClaimOutcome`."""


class ChunkNotFoundError(HubClientError):
    """The hub reports a chunk unknown (404) — terminal, not transient.

    Still a :class:`HubClientError`, so an unaware caller degrades to the retry behavior."""


class ChunkEndedError(HubClientError):
    """The hub refuses a chunk-scoped call because the chunk has ended (409) — terminal, not transient:
    an envelope read with no current runner node, or a rekey of a route on a ``done``/``stopped`` chunk —
    or because it has no live route (404 on a rekey).
    ``detail`` is the hub's refusal text. Still a :class:`HubClientError`, so an unaware caller retries."""

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


class IdentityRouteMissing(HubClientError):
    """The hub answered the identity read 404: it predates ``GET /api/fleet/identity``, so the runner
    learns which runner its token belongs to from its registration's reply instead."""


@domain_model
@dataclass(frozen=True)
class VersionRefusal:
    """The hub's refusal of this runner's version; ``detail`` names what to upgrade."""

    reason: VersionRefusalReason
    runner_version: str | None
    hub_version: str
    floor: str
    detail: str


class RunnerVersionRefused(HubClientError):
    """The hub's window refused a registration (409); a :class:`HubClientError`, read as an outage if unhandled."""

    def __init__(self, refusal: VersionRefusal) -> None:
        super().__init__(refusal.detail)
        self.refusal = refusal


class RunnerAddRefusalReason(StrEnum):
    """Why the hub refused to add a runner: ``UNSUPPORTED``, the hub predates adding runners under a
    host (404); ``NO_SUCH_PROJECT``, a named project is none the tenant holds (422)."""

    UNSUPPORTED = "unsupported"
    NO_SUCH_PROJECT = "no_such_project"


class RunnerAddRefused(HubClientError):
    """The hub answered an add with a refusal — terminal for the caller, never retried. ``reason``
    says which; ``detail`` is the hub's refusal text. Still a :class:`HubClientError`."""

    def __init__(self, message: str, *, reason: RunnerAddRefusalReason, detail: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = detail


class HostAddRefusalReason(StrEnum):
    """Why the hub refused to add a host: ``SIGN_IN_REQUIRED``, the hub requires an operator session
    and none was presented, or it was not accepted (401); ``FORBIDDEN``, the operator lacks the
    permission (403); ``UNSUPPORTED``, the hub predates hosts (404 or 405); ``NAME_TAKEN``, a live
    host of the tenant holds the name (409); ``TENANT_REQUIRED``, the operator may enter several
    tenants and named none (409); ``NO_SUCH_TENANT``, the named tenant is none the operator may
    enter (404)."""

    SIGN_IN_REQUIRED = "sign_in_required"
    FORBIDDEN = "forbidden"
    UNSUPPORTED = "unsupported"
    NAME_TAKEN = "name_taken"
    TENANT_REQUIRED = "tenant_required"
    NO_SUCH_TENANT = "no_such_tenant"


class HostAddRefused(HubClientError):
    """The hub answered a host add with a refusal — terminal for the caller, never retried.
    ``reason`` says which; ``detail`` is the hub's refusal text. Still a :class:`HubClientError`."""

    def __init__(self, message: str, *, reason: HostAddRefusalReason, detail: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.detail = detail


class HostRunnerRetireRefused(HubClientError):
    """The hub refused to retire a runner under the host because it still holds chunks (409) — terminal
    for the caller unless it retires again with ``force``. ``detail`` is the hub's refusal text, naming
    each held chunk. Still a :class:`HubClientError`."""

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


class HostRefusalReason(StrEnum):
    """Why the hub refused a call under the host's own token: ``CREDENTIAL_REFUSED``, the token is
    missing, unknown, or revoked (401); ``RETIRED``, its host is retired (403); ``NOT_FOUND``, the hub answered 404 —
    on every host route but retire, a hub that predates hosts; on retire, also a runner that does not
    stand under the host; ``VERSION_REFUSED``, the window refuses the host's version (409)."""

    CREDENTIAL_REFUSED = "credential_refused"
    RETIRED = "retired"
    NOT_FOUND = "not_found"
    VERSION_REFUSED = "version_refused"


@domain_model
@dataclass(frozen=True)
class HostRefused:
    """The hub's typed refusal of a host call: its ``reason``, the status it answered, and its
    refusal text."""

    reason: HostRefusalReason
    status: int
    detail: str


@domain_model
@dataclass(frozen=True)
class HostReportedWorkspace:
    """One workspace a host reports: its id, and how many environments it holds — ``None`` when it
    reports none."""

    workspace_id: str
    env_capacity: int | None = None


@domain_model
@dataclass(frozen=True)
class HostReport:
    """What a host reports about itself each tick: its public URL, the panel's sign-in callback
    URIs, its workspaces, and the subscriptions it declares. The hub replaces what it holds with
    ``workspaces`` and ``subscriptions`` whole."""

    url: str | None = None
    redirect_uris: tuple[str, ...] = ()
    workspaces: tuple[HostReportedWorkspace, ...] = ()
    subscriptions: tuple[SubscriptionDeclaration, ...] = ()


@domain_model
@dataclass(frozen=True)
class HostHeldRunner:
    """One runner the hub holds under a host: its id and the name the hub holds for it."""

    runner_id: str
    runner_name: str


@domain_model
@dataclass(frozen=True)
class HostRecord:
    """A host's own record as the hub answers it: the hub-minted ``hs_`` id, its tenant, the name
    the hub holds, and the runners it holds under the host that are not retired."""

    host_id: str
    tenant_id: str
    host_name: str
    runners: tuple[HostHeldRunner, ...] = ()


@domain_model
@dataclass(frozen=True)
class HostFactPushAck:
    """The hub's acknowledgement of one host-lane push against the host's own high-water mark: the
    new mark, and the pushed seqs partitioned into applied, already applied, and rejected for a
    non-idempotency reason."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]


@domain_model
@dataclass(frozen=True)
class RetiredHostRunner:
    """A runner the hub just retired under the host, and the chunks the retire released from it."""

    runner_id: str
    released_chunk_ids: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class IssuedHost:
    """A host the hub just added: its minted ``hs_`` id, its name, and the plaintext bearer token
    minted with the id — shown by the hub this once."""

    host_id: str
    host_name: str
    token: str


@domain_model
@dataclass(frozen=True)
class QueueWorkRef:
    """One work pointer a ready chunk carries: its source and the source-native ref."""

    source: str
    ref: str


@domain_model
@dataclass(frozen=True)
class QueueBlock:
    """A ready chunk's blocked marking: its earliest-declared unmet prerequisite, and how many
    prerequisites are unmet."""

    prerequisite_chunk_id: str
    unmet_count: int = 1


@domain_model
@dataclass(frozen=True)
class QueueEntry:
    """One ready chunk, in the hub's queue order."""

    chunk_id: str
    graph_id: str
    position: int
    work_refs: list[QueueWorkRef] = field(default_factory=list)
    blocked: QueueBlock | None = None
    #: The chunk's project, as the hub names it; ``None`` from a hub that does not say.
    project_id: str | None = None


@domain_model
@dataclass(frozen=True)
class SubscriptionDeclaration:
    """One provider subscription this runner declares at registration — the key every usage
    fact names it by."""

    slug: str
    name: str
    provider: str


@domain_model
@dataclass(frozen=True)
class BindingDeclaration:
    """One project this runner serves through a workspace of its host, with the agents it runs there at once."""

    project: str
    workspace_id: str
    agents: int


@domain_model
@dataclass(frozen=True)
class HubQuestion:
    """A worker's question as the hub holds it, with its derived answer and delivery state."""

    question_id: str
    chunk_id: str
    runner_id: str
    epoch: int
    question: str
    asked_at: str
    node_id: str | None = None
    session_id: str | None = None
    harness_id: str | None = None
    options: list[str] = field(default_factory=list)
    answered: bool = False
    answer: str | None = None
    answered_by: str | None = None
    answered_at: str | None = None
    delivered: bool = False
    delivered_at: str | None = None


@domain_model
@dataclass(frozen=True)
class ClaimRequest:
    """A complete route the claiming runner asks the hub for: the chunk, its workspace, and the
    environments it already bound. The hub knows the claimant from its token."""

    chunk_id: str
    workspace_id: str
    environment_ids: list[str]


@domain_model
@dataclass(frozen=True)
class ClaimedRoute:
    """A won claim — the route, the chunk's first node envelope, and the route's plaintext
    capability token, which the hub returns exactly once."""

    chunk_id: str
    runner_id: str
    workspace_id: str
    environment_ids: list[str]
    envelope: Envelope
    route_token: str


@domain_model
@dataclass(frozen=True)
class ClaimConflict:
    """A lost race: another runner holds the chunk's route."""

    chunk_id: str
    held_by_runner_id: str
    detail: str = "chunk already claimed"


@domain_model
@dataclass(frozen=True)
class TerminalDenial:
    """The chunk stands at a status no claim is legal from — ended for good (``done``,
    ``stopped``) or not ready yet."""

    chunk_id: str
    status: str
    detail: str = "chunk is terminal"


@domain_model
@dataclass(frozen=True)
class DependencyDenial:
    """The chunk stands on a prerequisite that has not reached ``done``."""

    chunk_id: str
    prerequisite_chunk_id: str
    detail: str = "chunk depends on an unmet prerequisite"


@domain_model
@dataclass(frozen=True)
class IncompatibleDenial:
    """The hub will not give this runner the chunk: ``reason`` says what it lacks — the capabilities
    to run every lineage the chunk can reach, or the chunk's project among those it serves, which
    ``detail`` then names. Either way the runner moves on; the reason is for its log."""

    chunk_id: str
    incompatible_runner_id: str
    detail: str = "runner capabilities no longer satisfy the chunk's reachable lineage"
    reason: ClaimDenialReason = ClaimDenialReason.CAPABILITY


@domain_model
@dataclass(frozen=True)
class PausedDenial:
    """The hub refuses this runner itself — paused, retired, or unregistered — and ``detail``
    names which."""

    chunk_id: str
    runner_id: str
    detail: str = "runner is paused at the hub"


@domain_model
@dataclass(frozen=True)
class RouteClaimOutcome:
    """The result of a route claim: exactly one of ``claimed`` / ``conflict`` /
    ``denied_paused`` / ``denied_terminal`` / ``denied_dependency`` / ``denied_incompatible``
    set — construction refuses any other count. A conflict is a race this claim lost; every
    denial means the hub refused it before any race."""

    claimed: ClaimedRoute | None = None
    conflict: ClaimConflict | None = None
    denied_paused: PausedDenial | None = None
    denied_terminal: TerminalDenial | None = None
    denied_dependency: DependencyDenial | None = None
    denied_incompatible: IncompatibleDenial | None = None

    def __post_init__(self) -> None:
        arms = (
            self.claimed,
            self.conflict,
            self.denied_paused,
            self.denied_terminal,
            self.denied_dependency,
            self.denied_incompatible,
        )
        set_count = sum(arm is not None for arm in arms)
        if set_count != 1:
            raise ValueError(f"a route claim outcome sets exactly one arm, not {set_count}")

    @property
    def won(self) -> bool:  # ast-grep-ignore: bzh:property-delegates
        return self.claimed is not None


@domain_model
@dataclass(frozen=True)
class PushedFact:
    """One buffered runner fact as it is pushed: its per-runner ``seq``, its ``noun.verb`` kind,
    and its kind-specific payload as the stored JSON object text, sent undecoded."""

    seq: int
    kind: str
    payload_text: str


@domain_model
@dataclass(frozen=True)
class FactPushAck:
    """The hub's acknowledgement of one push against its high-water mark: the new mark, and the
    pushed seqs partitioned into applied, already applied, and rejected for a non-idempotency
    reason. ``route_ended`` is the subset of ``rejected`` the hub refused because the fact's chunk
    has no live route."""

    high_water: int
    applied: list[int]
    already_applied: list[int]
    rejected: list[int]
    route_ended: list[int]


@domain_model
@dataclass(frozen=True)
class TranscriptPush:
    """One buffered transcript record as it is pushed: its lane ``seq`` and its body — the
    record's every field but ``seq``, in the canonical turn format."""

    seq: int
    body: dict[str, Any]


@domain_model
@dataclass(frozen=True)
class TranscriptPushAck:
    """The hub's acknowledgement of one transcript push against the lane's high-water mark.
    ``capped`` records are acknowledged with their content dropped; ``refused`` ones, whose
    lease epoch another holder owns, are acknowledged and never stored."""

    high_water: int
    applied: list[int] = field(default_factory=list)
    already_applied: list[int] = field(default_factory=list)
    capped: list[int] = field(default_factory=list)
    refused: list[int] = field(default_factory=list)


@domain_model
@dataclass(frozen=True)
class HubVersionWindow:
    """The hub's compatibility window: the oldest major.minor line it admits, and the newest, which is its own."""

    floor: str
    ceiling: str


@domain_model
@dataclass(frozen=True)
class RegistrationReply:
    """The hub's reply to a successful registration. ``tenant_*``, ``projects`` (one per declared entry as the hub
    resolved it), ``hub_version`` and ``window`` are ``None`` from a hub that predates them."""

    runner_id: str
    runner_name: str
    first_registration: bool
    tenant_id: str | None = None
    tenant_name: str | None = None
    projects: tuple[ServedProject, ...] | None = None
    hub_version: str | None = None
    window: HubVersionWindow | None = None


@domain_model
@dataclass(frozen=True)
class TokenIdentity:
    """The runner the hub says a presented bearer token belongs to — its id and current name, and
    its tenant's id and name (``None`` from a hub that predates tenants), and the hub's ``fleet`` flags (empty from
    a hub that predates them)."""

    runner_id: str
    runner_name: str
    tenant_id: str | None = None
    tenant_name: str | None = None
    fleet_flags: Mapping[str, bool] = field(default_factory=dict)

    def serves(self, tenant_id: str | None) -> bool:
        """Whether this runner serves ``tenant_id``; naming no tenant, or a hub that names none, settles none."""
        return tenant_id is None or self.tenant_id is None or self.tenant_id == tenant_id


@domain_model
@dataclass(frozen=True)
class TokenRefusal:
    """The hub's refusal of a presented bearer token. ``runner_id`` names the runner a ``revoked``
    or ``retired`` token was issued to; ``None`` for a ``missing`` or ``unknown`` one."""

    reason: RunnerTokenRefusalReason
    runner_id: str | None = None


@domain_model
@dataclass(frozen=True)
class OperatorTenant:
    """A tenant the operator may add a runner in — its id, the only way to name it, and its name."""

    tenant_id: str
    name: str


@domain_model
@dataclass(frozen=True)
class IssuedIdentity:
    """A runner the hub just added: its minted id, its initial name, and the plaintext bearer token
    minted with the id — shown by the hub this once."""

    runner_id: str
    runner_name: str
    token: str


class IChunkStatusReader(Protocol):
    """The chunk-status read alone, narrowed from :class:`IHubClient` (``bzh:seam-size-ceiling``)."""

    def chunk_statuses(self, chunk_ids: Iterable[str]) -> dict[str, ChunkState]:
        """``GET /api/fleet/chunk-statuses`` (repeatable ``chunk_id``) — every requested id
        present in the store, keyed by ``chunk_id``; an id the hub doesn't know is simply
        absent, never an error. A transport/5xx failure raises ``HubClientError`` for the
        whole call."""
        ...


class ITranscriptPusher(Protocol):
    """The transcript push alone, narrowed from :class:`IHubClient` (``bzh:seam-size-ceiling``)."""

    def push_transcripts(self, records: Sequence[TranscriptPush]) -> TranscriptPushAck:
        """``POST /api/fleet/transcripts`` — the transcript lane's own store-and-forward
        push, seq-idempotent against its own high-water mark. Structurally independent
        of :meth:`push_facts`: a wedged or slow
        transcript flush never blocks it."""
        ...


class IHubClient(IChunkStatusReader, ITranscriptPusher, Protocol):
    """The runner's client of the hub API. Outbound-only."""

    def peek_queue(
        self, capabilities: Sequence[HarnessCapability], *, policy: str, project: str | None = None
    ) -> list[QueueEntry]:
        """``POST /api/fleet/queue/peek`` — at most one queue entry matching ``capabilities``
        under ``policy``, narrowed to ``project`` when one is named; a refusal raises ``HubClientError``."""
        ...

    def claim_route(self, claim: ClaimRequest) -> RouteClaimOutcome:
        """``POST /api/fleet/routes`` — claim work; 409 loses the race (or, distinctly,
        the chunk is already terminal or stands on an unmet prerequisite), 403 means
        the hub registry already has this runner paused."""
        ...

    def submit_completion(self, chunk_id: str, completion: Completion) -> ApplyReply:
        """``POST /api/fleet/chunks/{id}/completions`` — the atomic, epoch-fenced write."""
        ...

    def submit_decision(self, chunk_id: str, gate: GateSubmission) -> ApplyReply:
        """``POST /api/fleet/chunks/{id}/decisions`` — a runner-config gate parks the chunk."""
        ...

    def push_facts(self, facts: Sequence[PushedFact]) -> FactPushAck:
        """``POST /api/fleet/events`` — store-and-forward fact push, seq-idempotent."""
        ...

    def get_envelope(self, chunk_id: str) -> Envelope:
        """``GET /api/fleet/chunks/{id}/envelope`` — the idempotent envelope re-read. Raises
        :class:`ChunkNotFoundError` for an unknown chunk and :class:`ChunkEndedError` for one
        that has ended."""
        ...

    def hub_advance(self, chunk_id: str) -> HubAdvanceResponse:
        """``POST /api/fleet/chunks/{id}/hub-advance`` — drive a chunk parked at a generic
        hub command node one step.

        ``ran=False`` means the hub declined to run a step this call; a later call may run it."""
        ...

    def get_question(self, question_id: str) -> HubQuestion:
        """``GET /api/fleet/questions/{id}`` — the runner's answer poll, by question id."""
        ...

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
        subscription: str | None = None,
    ) -> RegistrationReply:
        """``POST /api/fleet/runners`` — register the runner this client's token names under its
        declared ``name``, and learn its hub-minted id from the reply. Idempotent, and refreshes the
        runner's liveness at the hub. Every optional field is unconditionally overwritten each call
        but ``subscriptions``, which ``None`` omits — a runner of a registered host, whose host reports the
        roster, sends none.
        ``gates`` is the runner's own configured human-gate node names — reported for display, never
        read back to enforce. ``projects`` is the projects the runner serves, by id or slug: sent as
        given, ``None`` as null — never ``[]`` — so the hub keeps the declaration it holds, where
        ``()`` declares that the runner serves none. ``bindings`` is the same declaration by workspace and agent count,
        which a hub that reads it takes over ``projects`` for; ``subscription`` is the slug of the declared
        subscription the runner spends, ``None`` naming none. A refused version raises
        :class:`RunnerVersionRefused`, a refused token :class:`HubClientError`."""
        ...

    def fetch_runner_paused(self, runner_id: str) -> bool:
        """``GET /api/fleet/runners/{id}`` — the runner's declarative pause brake, read outbound."""
        ...

    def rekey_route_token(self, chunk_id: str) -> str:
        """``POST /api/fleet/chunks/{id}/route-token`` — rotate the chunk's route
        capability token. Why it exists: `src/blizzard/hub/domain/execution/claim.py`'s
        ``ClaimService.rekey``. Raises :class:`ChunkEndedError` when the live route sits on an
        ended chunk (409) or the chunk has no live route (404)."""
        ...


class ITokenIdentityReader(Protocol):
    """Ask the hub who this client's runner bearer token belongs to (``bzh:seam-size-ceiling``)."""

    def identity(self) -> TokenIdentity | TokenRefusal:
        """``GET /api/fleet/identity`` — the token's runner, or the hub's typed ``401`` refusal
        telling a missing, unknown, revoked, and retired token apart. Side-effect free at the hub:
        it registers nothing and records no liveness. Any other answer — unreachable, a 5xx, or a
        hub without the route (404) — raises :class:`HubClientError`."""
        ...


class IRunnerAdder(Protocol):
    """Add a runner under the credential that stands behind the caller (``bzh:seam-size-ceiling``)."""

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity:
        """Add a runner under the initial ``name``, serving the projects ``projects`` names — or, with
        ``None``, the tenant's only project; the hub mints its id and bearer token together. A refusal
        raises :class:`RunnerAddRefused`; an unreachable hub or a 5xx raises :class:`HubClientError`,
        and the hub has added nothing the caller can use."""
        ...


class IHubRunnerAdmin(Protocol):
    """The runner-side calls made under an operator's credential rather than a runner token: adding
    a host, and listing the tenants the operator may add one in. Its credential is the operator's
    signed-in session for the hub when one is held, and none otherwise — never a runner token. A hub
    without sign-in admits no credential; any other answers 401."""

    def tenants(self) -> list[OperatorTenant]:
        """``GET /api/me`` — every tenant the operator may enter, ordered by id; empty from a hub
        that predates tenants. Any failure raises :class:`HubClientError`."""
        ...

    def add_host(self, name: str, *, tenant_id: str | None = None, adopt: tuple[str, ...] = ()) -> IssuedHost:
        """``POST /api/hosts`` — add a host under ``name`` in the tenant ``tenant_id`` names, or, with
        none named, the one the hub resolves for the operator, placing the runners ``adopt`` names under
        it in the same write; the hub mints its id and bearer token together. A refusal raises
        :class:`HostAddRefused`; an unreachable hub or a 5xx raises :class:`HubClientError`, and the hub
        has added nothing the caller can use."""
        ...


class IHostHubClient(Protocol):
    """The calls a host makes under its own bearer token, every one under ``/api/fleet/host``. The hub
    takes the host from the token alone. Each answers its result, or :class:`HostRefused` when the hub
    refuses the token (401, 403) or answers 404; an unreachable hub, a 5xx, or any other refusal raises
    :class:`HubClientError`."""

    def report(self, report: HostReport) -> HostRecord | HostRefused:
        """``POST /api/fleet/host`` — report the host and its version, and read its record back. Never refused
        over its content — it doubles as the heartbeat — but a refused version answers ``VERSION_REFUSED``."""
        ...

    def record(self) -> HostRecord | HostRefused:
        """``GET /api/fleet/host`` — the host's own record, reporting nothing."""
        ...

    def push_facts(self, facts: Sequence[PushedFact]) -> HostFactPushAck | HostRefused:
        """``POST /api/fleet/host/events`` — push the host's own buffered facts, ordered by seq,
        de-duplicated at the hub against the host's own mark."""
        ...

    def add_runner(self, name: str, *, projects: tuple[str, ...] | None = None) -> IssuedIdentity | HostRefused:
        """``POST /api/fleet/host/runners`` — add a runner under the host, in its tenant, under the
        initial ``name``, serving the projects ``projects`` names — or, with ``None``, the tenant's only
        project; the hub mints its id and bearer token together. A named project the tenant does not hold
        raises :class:`RunnerAddRefused`."""
        ...

    def retire_runner(self, runner_id: str, *, force: bool = False) -> RetiredHostRunner | HostRefused:
        """``POST /api/fleet/host/runners/{runner_id}/retire`` — retire a runner under the host. The hub
        refuses one that still holds chunks unless ``force`` releases them, raising
        :class:`HostRunnerRetireRefused` naming them."""
        ...
