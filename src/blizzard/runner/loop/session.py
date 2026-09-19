"""Which prior session a node-entry spawn resumes, and the session stamps it runs under."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.domain.leases import (
    IReadLeaseSessionRepository,
    LeaseRecord,
    PoolHead,
)
from blizzard.runner.harness.adapter import IHarnessModelResolution
from blizzard.runner.harness.identity import SessionReference
from blizzard.runner.harness.registry import IHarnessRegistry, UnavailableHarnessError, UnknownHarnessError
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.wire.envelope import TIER_PREFIX, NodeConfig

_log = get_logger("blizzard.runner.loop")


@dataclass(frozen=True)
class ResumeTarget:
    """A node-entry spawn's resume target — the session to resume (``None`` for a fresh
    mint), paired with the owner a rotated named pool's replacement must mint under, and,
    when an existing session's owner cannot be dispatched to, the exception that says why."""

    session: SessionReference | None
    #: The breached pool head's own owner to mint its replacement under; unset for a plain resume or no breach.
    pool_owner: str | None = None
    #: The existing session whose recorded owner won't resolve, paired with why; escalate in place, never mint under it.
    owner_unresolvable: tuple[SessionReference, UnknownHarnessError | UnavailableHarnessError] | None = None


@dataclass(frozen=True)
class ResumedSession:
    """The session a spawn resumes, bound to its newest recorded lease — one value, so no
    caller can pair one spawn's session with another's lease."""

    session: SessionReference
    lease: LeaseRecord | None

    @property
    def session_id(self) -> str:
        """The operator-visible raw session id the recorded owner issued, whichever owner
        that is."""
        return self.session.session_id


@dataclass(frozen=True)
class SessionResolver:
    """Resolves a spawn's session identity against the store's own session history."""

    leases: IReadLeaseSessionRepository
    #: Required; every recorded session's owner resolves through this registry, with no single-harness fallback.
    harnesses: IHarnessRegistry
    #: The transcripts lane's on/off switch — every actual read still dispatches per-owner through ``harnesses``.
    transcripts_wired: bool = False

    def resolve_resume(self, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> ResumeTarget:
        """The prior session this spawn resumes, or ``None`` to mint fresh (#115, #144), paired
        with the owner a rotated named pool's replacement must mint under. **Only the
        resume-vs-mint decision** — the configuration a spawn runs under resolves in
        ``session_stamps``. No match anywhere falls back to fresh: a resume target is
        best-effort."""
        if node.session is SessionMode.FRESH:
            return ResumeTarget(session=None)
        if node.session_name is not None:
            return self._pool_resume(chunk_id, node, spawn_cwd)
        return self._plain_resume(chunk_id, node)

    def _plain_resume(self, chunk_id: str, node: NodeConfig) -> ResumeTarget:
        """A bare, un-pooled resume's latest session, or ``None`` to mint fresh — its own
        owner checked here too, exactly as a named pool's head is: an unresolvable owner
        returns alongside the session as ``ResumeTarget.owner_unresolvable``, rather than
        silently falling back to a fresh mint."""
        session = self.leases.latest_session(chunk_id, node.session_source)
        if session is None:
            return ResumeTarget(session=None)
        exc = self._unresolvable_owner(session.harness_id)
        if exc is not None:
            _log.error(
                "plain resume blocked by unavailable harness owner",
                harness_id=session.harness_id,
                detail=str(exc),
            )
            return ResumeTarget(session=None, owner_unresolvable=(session, exc))
        return ResumeTarget(session=session)

    def resumption(self, resume_from: SessionReference | None) -> ResumedSession | None:
        """The session this spawn resumes with its newest recorded lease, or ``None`` for a
        fresh mint (blizzard#340). Empty matches the adapter's own predicate: a blank
        ``resume_from`` is a brand-new session, never a lookup key (issue #149)."""
        if resume_from is None:
            return None
        return ResumedSession(session=resume_from, lease=self.leases.lease_for_session(resume_from))

    def session_stamps(
        self, node: NodeConfig, resume: ResumedSession | None, *, harness_id: str
    ) -> tuple[str | None, str | None, str | None]:
        """The (model, effort, compaction_window) this spawn runs under, and stamps (#144, blizzard#343).

        **The stamp describes the session, not the preference.** A spawn that *resumes* inherits
        all three from the resumed session's own recorded lease, riding ``resume`` from
        :meth:`resumption` — and an inherited ``None`` stays *unknown*."""
        if resume is not None:
            if resume.lease is None:
                return (None, None, None)
            return (resume.lease.resolved_model, resume.lease.resolved_effort, resume.lease.resolved_compaction_window)
        harness = self._resolved_harness(harness_id)
        model = harness.resolve_model(node.session_model)
        return (
            model,
            harness.resolve_effort(node.session_effort),
            harness.resolve_compaction_window(node.session_compaction_window),
        )

    def _pool_resume(self, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> ResumeTarget:
        """The named pool's head if it is still resumable, else a fresh mint paired with the
        breached head's own owner (``None`` session to mint a new one) — or, when the breach
        IS that owner failing to resolve, ``owner_unresolvable`` set instead of a mint
        target."""
        pool = node.session_name or ""
        head = self.leases.pool_head(chunk_id, pool)
        if head is None:
            return ResumeTarget(session=None)  # an empty pool — this member mints the head
        breach, owner_exc = self._rotation_breach(head, node, spawn_cwd)
        if breach is None:
            return ResumeTarget(session=head.session)
        _log.info(
            "rotating session pool",
            chunk_id=chunk_id,
            session_pool=pool,
            breached=breach,
            old_session_id=head.session_id,
        )
        return ResumeTarget(
            session=None,
            pool_owner=head.session.harness_id,
            owner_unresolvable=(head.session, owner_exc) if owner_exc is not None else None,
        )

    def _rotation_breach(
        self, head: PoolHead, node: NodeConfig, spawn_cwd: str | None
    ) -> tuple[str | None, UnknownHarnessError | UnavailableHarnessError | None]:
        """Why this pool head must not be resumed, or ``None`` when it may be (issue #144),
        paired with the owner's own unresolvable exception. A head resumes only while every
        *readable* threshold is under bound and its model still matches; an unreadable signal,
        including an unresolvable transcript source, is never a breach — the owner's own
        unresolvable read is the one exception, itself always a breach."""
        try:
            harness = self.harnesses.adapter(head.session.harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            _log.error(
                "session pool rotation check blocked by unavailable harness owner",
                harness_id=head.session.harness_id,
                detail=str(exc),
            )
            return "owner-unresolvable", exc
        # Model drift first: the one check that needs no telemetry, and an edited declaration
        # should rotate regardless of how much context the old head accumulated.
        resolved = harness.resolve_model(node.session_model) if node.session_model else None
        if resolved is not None and head.resolved_model is not None and head.resolved_model != resolved:
            return "model-drift", None

        rotate = node.session_rotate
        if rotate is None:
            return None, None  # the declaration bounds nothing

        needs_transcript = rotate.max_context_tokens is not None or rotate.max_transcript_bytes is not None
        # An unresolvable transcript source is just another unreadable signal below: no
        # binding (or an off transcripts lane) leaves those checks unmeasured, not forced.
        source = self._resolve_transcript_source(head.session) if needs_transcript and self.transcripts_wired else None

        if rotate.max_context_tokens is not None and source is not None:
            # The transcript, never the usage facts: only it records per-turn prompt sizes, and
            # a usage row's cumulative figure is not this quantity (`Record.context_tokens`).
            tokens = source.context_tokens(head.session_id, spawn_cwd=spawn_cwd)
            if tokens is not None and tokens > rotate.max_context_tokens:
                return "max_context_tokens", None

        # A count is never an unknown — it is the number of rows that exist.
        if (
            rotate.max_invocations is not None
            and self.leases.session_invocation_count(head.session) > rotate.max_invocations
        ):
            return "max_invocations", None

        if rotate.max_transcript_bytes is not None and source is not None:
            # `size_bytes` returns `None` for an unreadable transcript — treated as unknown,
            # never a zero that would make the threshold silently inert.
            size = source.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
            if size is not None and size > rotate.max_transcript_bytes:
                return "max_transcript_bytes", None

        return None, None

    def _unresolvable_owner(self, harness_id: str) -> UnknownHarnessError | UnavailableHarnessError | None:
        """Whether ``harness_id`` resolves right now — ``None`` when it does, else the
        exception that says why not. :meth:`_plain_resume`'s own check; :meth:`_rotation_breach`
        keeps its inline resolve since it needs the adapter itself for the checks past it."""
        try:
            self.harnesses.adapter(harness_id)
        except (UnknownHarnessError, UnavailableHarnessError) as exc:
            return exc
        return None

    def _resolved_harness(self, harness_id: str) -> IHarnessModelResolution:
        """Resolve ``harness_id``, raising ``UnknownHarnessError``/``UnavailableHarnessError``
        on an unknown or unavailable one — callable only where the id is already guaranteed
        to resolve."""
        return self.harnesses.adapter(harness_id)

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


@dataclass(frozen=True)
class SkippedHarness:
    """One acceptable-set member :class:`HarnessSelector` passed over, and why —
    the account an exhausted selection escalates with."""

    harness_id: str
    reason: str  # "unknown" | "unavailable" | "no-authored-tier"


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

    def select(self, node: NodeConfig) -> HarnessSelection:
        """The earliest member of ``node.session_harnesses`` this runner can dispatch to, in
        declared order — a member the registry cannot serve is skipped and recorded. A single
        member skips the model check only when nothing in ``node.session_model`` is an
        authored (``blizzard:``-namespaced) tier; an authored tier this harness cannot map is
        never silently substituted (worker-spawn.md) — skipped like a larger set's own member."""
        members = node.session_harnesses
        strict = bool(node.session_model) and (
            len(members) > 1 or any(preference.startswith(TIER_PREFIX) for preference in node.session_model)
        )
        skipped: list[SkippedHarness] = []
        for harness_id in members:
            try:
                adapter = self.harnesses.adapter(harness_id)
            except UnknownHarnessError:
                skipped.append(SkippedHarness(harness_id, "unknown"))
                continue
            except UnavailableHarnessError:
                skipped.append(SkippedHarness(harness_id, "unavailable"))
                continue
            if strict and adapter.resolve_model_strict(node.session_model) is None:
                skipped.append(SkippedHarness(harness_id, "no-authored-tier"))
                continue
            return HarnessSelection(harness_id=harness_id, skipped=tuple(skipped))
        return HarnessSelection(harness_id=None, skipped=tuple(skipped))
