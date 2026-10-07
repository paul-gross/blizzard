"""Which prior session a node-entry spawn resumes, and the session stamps it runs under."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import TIER_PREFIX, SessionMode
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.health import reported_health
from blizzard.runner.harness.health_cache import IReadHarnessHealth
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.leases.model import Lease, PoolHead
from blizzard.runner.leases.session import IReadLeaseSessionRepository
from blizzard.runner.node_steps.envelope import EnvelopeNode, RotateBounds

_log = get_logger("blizzard.runner.loop")


@domain_model
@dataclass(frozen=True)
class ResumeTarget:
    """A node-entry spawn's resume target — the session to resume (``None`` for a fresh
    mint, which a breached pool's replacement is), paired, when an existing session's owner
    cannot be dispatched to, with the exception that says why."""

    session: SessionReference | None
    #: The existing session whose recorded owner won't resolve, paired with why; escalate in place, never mint under it.
    owner_unresolvable: tuple[SessionReference, UnknownHarnessError | UnavailableHarnessError] | None = None


@domain_model
@dataclass(frozen=True)
class ResumedSession:
    """The session a spawn resumes, bound to its newest recorded lease — one value, so no
    caller can pair one spawn's session with another's lease."""

    session: SessionReference
    lease: Lease | None

    @property
    def session_id(self) -> str:
        """The operator-visible raw session id the recorded owner issued, whichever owner
        that is."""
        return self.session.session_id

    def inherited_stamps(self) -> tuple[str | None, str | None, str | None]:
        """The (model, effort, compaction_window) a resume runs under: all three inherited from the
        resumed session's own recorded lease. **The stamp describes the session, not the
        preference** — an inherited ``None``, or no recorded lease at all, stays unknown."""
        if self.lease is None:
            return (None, None, None)
        return (self.lease.resolved_model, self.lease.resolved_effort, self.lease.resolved_compaction_window)


@dataclass(frozen=True)
class SessionResolver:
    """Resolves a spawn's session identity against the store's own session history."""

    leases: IReadLeaseSessionRepository
    #: Required; every recorded session's owner resolves through this registry, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The transcripts lane's on/off switch — every actual read still dispatches per-owner through ``harnesses``.
    transcripts_wired: bool = False

    def resolve_resume(self, chunk_id: str, node: EnvelopeNode, spawn_cwd: str | None) -> ResumeTarget:
        """The prior session this spawn resumes, or ``None`` to mint fresh, paired
        with the owner a rotated named pool's replacement must mint under. **Only the
        resume-vs-mint decision** — the configuration a spawn runs under resolves in
        ``session_stamps``. Loads the facts :func:`resume_target` decides over."""
        if node.session is SessionMode.FRESH:
            return resume_target(node)
        if node.session_name is not None:
            return self._pool_resume(chunk_id, node, spawn_cwd)
        return self._plain_resume(chunk_id, node)

    def _plain_resume(self, chunk_id: str, node: EnvelopeNode) -> ResumeTarget:
        """A bare, un-pooled resume's latest session, with its owner checked exactly as a named
        pool's head is."""
        session = self.leases.latest_session(chunk_id, node.session_source)
        exc = self._unresolvable_owner(session.harness_id) if session is not None else None
        if session is not None and exc is not None:
            _log.error(
                "plain resume blocked by unavailable harness owner",
                harness_id=session.harness_id,
                detail=str(exc),
            )
        return resume_target(node, candidate=session, owner_exc=exc)

    def resumption(self, resume_from: SessionReference | None) -> ResumedSession | None:
        """The session this spawn resumes with its newest recorded lease, or ``None`` for a
        fresh mint. Empty matches the adapter's own predicate: a blank
        ``resume_from`` is a brand-new session, never a lookup key."""
        if resume_from is None:
            return None
        return ResumedSession(session=resume_from, lease=self.leases.lease_for_session(resume_from))

    def session_stamps(
        self, node: EnvelopeNode, resume: ResumedSession | None, *, harness_id: str
    ) -> tuple[str | None, str | None, str | None]:
        """The (model, effort, compaction_window) this spawn runs under, and stamps. A resume's
        come from ``ResumedSession.inherited_stamps``."""
        if resume is not None:
            return resume.inherited_stamps()
        harness = self.harnesses.model_resolution(harness_id)
        model = harness.resolve_model(node.session_model)
        return (
            model,
            harness.resolve_effort(node.session_effort),
            harness.resolve_compaction_window(node.session_compaction_window),
        )

    def _pool_resume(self, chunk_id: str, node: EnvelopeNode, spawn_cwd: str | None) -> ResumeTarget:
        """The named pool's head and, when there is one, why it must not be resumed."""
        pool = node.session_name or ""
        head = self.leases.pool_head(chunk_id, pool)
        if head is None:
            return resume_target(node)  # an empty pool — this member mints the head
        breach, owner_exc = self._rotation_breach(head, node, spawn_cwd)
        if breach is not None:
            _log.info(
                "rotating session pool",
                chunk_id=chunk_id,
                session_pool=pool,
                breached=breach,
                old_session_id=head.session_id,
            )
        return resume_target(node, candidate=head.session, breach=breach, owner_exc=owner_exc)

    def _rotation_breach(
        self, head: PoolHead, node: EnvelopeNode, spawn_cwd: str | None
    ) -> tuple[str | None, UnknownHarnessError | UnavailableHarnessError | None]:
        """Why this pool head must not be resumed, or ``None`` when it may be,
        paired with the owner's own unresolvable exception. A head resumes only while every
        *readable* threshold is under bound and its model still matches; an unreadable signal,
        including an unresolvable transcript source, is never a breach — the owner's own
        unresolvable read is the one exception, itself always a breach."""
        try:
            harness = self.harnesses.model_resolution(head.session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "session pool rotation check blocked by unavailable harness owner",
                harness_id=head.session.harness_id,
                detail=str(exc),
            )
            return "owner-unresolvable", exc
        # Model drift first: the one check that needs no telemetry, and an edited declaration
        # should rotate regardless of how much context the old head accumulated.
        strict = bool(node.session_harnesses) and selection_is_strict(node)
        if node.session_model:
            resolved = (
                harness.resolve_model_strict(node.session_model)
                if strict
                else harness.resolve_model(node.session_model)
            )
        else:
            resolved = None
        model_breach = model_breach_reason(head.resolved_model, resolved, strict=strict)
        if model_breach is not None:
            return model_breach, None

        rotate = node.session_rotate
        if rotate is None:
            return None, None  # the declaration bounds nothing
        needs_transcript = rotate.max_context_tokens is not None or rotate.max_transcript_bytes is not None
        # An unresolvable transcript source leaves its checks unmeasured, not forced; each signal is read
        # only when its own bound is declared.
        source = self._resolve_transcript_source(head.session) if needs_transcript and self.transcripts_wired else None
        # The transcript, never the usage facts: only it records per-turn prompt sizes, and
        # a usage row's cumulative figure is not this quantity (`Record.context_tokens`).
        tokens = (
            source.context_tokens(head.session_id, spawn_cwd=spawn_cwd)
            if source is not None and rotate.max_context_tokens is not None
            else None
        )
        # A count is never an unknown — it is the number of rows that exist.
        invocations = self.leases.session_invocation_count(head.session) if rotate.max_invocations is not None else None
        size = (
            source.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
            if source is not None and rotate.max_transcript_bytes is not None
            else None
        )
        return rotation_breach(rotate, context_tokens=tokens, invocations=invocations, transcript_bytes=size), None

    def _unresolvable_owner(self, harness_id: str) -> UnknownHarnessError | UnavailableHarnessError | None:
        """Whether ``harness_id`` resolves right now — ``None`` when it does, else the
        exception that says why not. :meth:`_plain_resume`'s own check; :meth:`_rotation_breach`
        keeps its inline resolve since it needs the adapter itself for the checks past it."""
        try:
            self.harnesses.model_resolution(harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            return exc
        return None

    def _resolve_transcript_source(self, session: SessionReference) -> IHarnessTranscriptSource | None:
        """Resolve ``session``'s transcript source, logging and returning ``None`` — never
        raising — when it is unknown or unavailable. A harness's transcript source resolves
        independently of its adapter: binding one is no guarantee of the other."""
        try:
            return self.harnesses.transcript_source(session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "session pool rotation check blocked by unavailable harness transcript source",
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return None


@domain_model
@dataclass(frozen=True)
class SkippedHarness:
    """One acceptable-set member :class:`HarnessSelector` passed over, and why —
    the account an exhausted selection escalates with."""

    harness_id: str
    reason: str  # "unknown" | "unavailable" | "unhealthy" | "no-authored-tier" | "not-a-member"


@domain_model
@dataclass(frozen=True)
class HarnessSelection:
    """A fresh mint's resolved owner among a node's acceptable set, or ``None`` when nothing
    in it could serve — paired with why every skipped member was
    skipped, whether or not selection ultimately succeeded."""

    harness_id: str | None
    skipped: tuple[SkippedHarness, ...] = ()


@dataclass(frozen=True)
class HarnessSelector:
    """Chooses a fresh mint's owner among ``node.session_harnesses`` — deterministic
    orchestration over the adapter seam (``bzh:deterministic-shell``), handed the registry
    it walks rather than constructing one (``bzh:dependency-injection``). ``Spawner.spawn``
    is its only caller: a resume or a forced continuation never reaches selection at all."""

    harnesses: IHarnessRegistry
    #: This runner's own cross-tick health cache; ``None`` skips the health gate entirely.
    health: IReadHarnessHealth | None = None

    def select(self, node: EnvelopeNode) -> HarnessSelection:
        """The earliest member of ``node.session_harnesses`` this runner can dispatch to, in
        declared order — a member the registry cannot serve, or one health has withdrawn,
        is skipped and recorded. Whether the model check applies is ``selection_is_strict``'s."""
        strict = selection_is_strict(node)
        skipped: list[SkippedHarness] = []
        for harness_id in node.session_harnesses:
            try:
                adapter = self.harnesses.model_resolution(harness_id)
            except UnknownHarnessError:
                skipped.append(SkippedHarness(harness_id, "unknown"))
                continue
            except UnavailableHarnessError:
                skipped.append(SkippedHarness(harness_id, "unavailable"))
                continue
            healthy = self.health is None or reported_health(harness_id, self.health.get(harness_id)).available
            reason = member_skip_reason(
                healthy=healthy,
                maps_authored_tier=not strict or adapter.resolve_model_strict(node.session_model) is not None,
            )
            if reason is not None:
                skipped.append(SkippedHarness(harness_id, reason))
                continue
            return HarnessSelection(harness_id=harness_id, skipped=tuple(skipped))
        return HarnessSelection(harness_id=None, skipped=tuple(skipped))


def selection_is_strict(node: EnvelopeNode) -> bool:
    """Whether selection checks each member can map the node's model: a model preference set
    across several members, or one naming an authored (``blizzard:``-namespaced) tier — a tier a
    harness cannot map is never silently substituted."""
    members = node.session_harnesses
    return bool(node.session_model) and (
        len(members) > 1 or any(preference.startswith(TIER_PREFIX) for preference in node.session_model)
    )


def member_skip_reason(*, healthy: bool, maps_authored_tier: bool) -> str | None:
    """Why a resolvable acceptable-set member is passed over — health first, then the model
    check — or ``None`` when it may serve the mint."""
    if not healthy:
        return "unhealthy"
    if not maps_authored_tier:
        return "no-authored-tier"
    return None


def resume_target(
    node: EnvelopeNode,
    *,
    candidate: SessionReference | None = None,
    breach: str | None = None,
    owner_exc: UnknownHarnessError | UnavailableHarnessError | None = None,
) -> ResumeTarget:
    """A node-entry spawn's resume target: ``candidate`` is the plain resume's latest session or the
    pool's head, ``breach`` why a pool head must not resume, ``owner_exc`` why its owner will not resolve.
    A fresh node or no candidate mints fresh (best-effort). A plain resume resumes its candidate, escalating
    in place rather than minting when the owner is unresolvable. A pool head resumes while unbreached; a
    breached one's replacement is a fresh mint — its owner sourced by selection or the runner default, never
    carried from the head — or an in-place escalation when the breach is the head's owner."""
    if node.session is SessionMode.FRESH or candidate is None:
        return ResumeTarget(session=None)
    unresolvable = (candidate, owner_exc) if owner_exc is not None else None
    if node.session_name is None:
        if unresolvable is not None:
            return ResumeTarget(session=None, owner_unresolvable=unresolvable)
        return ResumeTarget(session=candidate)
    if breach is None:
        return ResumeTarget(session=candidate)
    return ResumeTarget(session=None, owner_unresolvable=unresolvable)


def model_drifted(head_model: str | None, resolved: str | None) -> bool:
    """A pool head rotates when the node now resolves to a different known model than the head
    recorded; an unknown on either side is not drift."""
    return resolved is not None and head_model is not None and head_model != resolved


def model_breach_reason(head_model: str | None, resolved: str | None, *, strict: bool) -> str | None:
    """Why the node's model preference breaches a pool head, or ``None``. ``strict`` mirrors selection
    over an authored harness set: the owner mapping none of the preference (``resolved`` is ``None``)
    is itself a breach, since the replacement could never be minted under it."""
    if strict and resolved is None:
        return "no-authored-tier"
    if model_drifted(head_model, resolved):
        return "model-drift"
    return None


def rotation_breach(
    rotate: RotateBounds, *, context_tokens: int | None, invocations: int | None, transcript_bytes: int | None
) -> str | None:
    """The first rotation bound a pool head has gone over, in declared order, or ``None``. An
    unreadable signal (``None``) is never a breach — never a zero that would make its bound inert."""
    if (
        rotate.max_context_tokens is not None
        and context_tokens is not None
        and context_tokens > rotate.max_context_tokens
    ):
        return "max_context_tokens"
    if rotate.max_invocations is not None and invocations is not None and invocations > rotate.max_invocations:
        return "max_invocations"
    if (
        rotate.max_transcript_bytes is not None
        and transcript_bytes is not None
        and transcript_bytes > rotate.max_transcript_bytes
    ):
        return "max_transcript_bytes"
    return None
