"""Which prior session a node-entry spawn resumes, and the session stamps it runs under."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import SessionMode
from blizzard.runner.domain.leases import (
    IReadLeaseRepository,
    LeaseRecord,
    PoolHead,
)
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.wire.envelope import NodeConfig

_log = get_logger("blizzard.runner.loop")


@dataclass(frozen=True)
class ResumedSession:
    """The session a spawn resumes, bound to its newest recorded lease — one value, so no
    caller can pair one spawn's session with another's lease."""

    session_id: str
    lease: LeaseRecord | None


@dataclass(frozen=True)
class SessionResolver:
    """Resolves a spawn's session identity against the store's own session history."""

    leases: IReadLeaseRepository
    harness: IHarnessAdapter
    transcripts: IHarnessTranscriptSource | None = None

    def resume_target(self, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
        """The prior session id this spawn resumes, or ``None`` to mint fresh (#115, #144).

        **Only the resume-vs-mint decision** — the configuration a spawn runs under resolves
        in ``session_stamps``. No match anywhere falls back to fresh: a resume target is
        best-effort."""
        if node.session is SessionMode.FRESH:
            return None
        if node.session_name is not None:
            return self._pool_head(chunk_id, node, spawn_cwd)
        return self.leases.latest_session_id(chunk_id, node.session_source)

    def resumption(self, resume_from: str | None) -> ResumedSession | None:
        """The session this spawn resumes with its newest recorded lease, or ``None`` for a
        fresh mint (blizzard#340). Empty matches the adapter's own predicate: a blank
        ``resume_from`` is a brand-new session, never a lookup key (issue #149)."""
        if not resume_from:
            return None
        return ResumedSession(session_id=resume_from, lease=self.leases.lease_for_session(resume_from))

    def session_stamps(
        self, node: NodeConfig, resume: ResumedSession | None
    ) -> tuple[str | None, str | None, str | None]:
        """The (model, effort, compaction_window) this spawn runs under, and stamps (#144, blizzard#343).

        **The stamp describes the session, not the preference.** A spawn that *resumes* inherits
        all three from the resumed session's own recorded lease, riding ``resume`` from
        :meth:`resumption` — and an inherited ``None`` stays *unknown*."""
        if resume is not None:
            if resume.lease is None:
                return (None, None, None)
            return (resume.lease.resolved_model, resume.lease.resolved_effort, resume.lease.resolved_compaction_window)
        model = self.harness.resolve_model(node.session_model)
        return (
            model,
            self.harness.resolve_effort(node.session_effort),
            self.harness.resolve_compaction_window(node.session_compaction_window),
        )

    def _pool_head(self, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
        """The named pool's head if it is still resumable, else ``None`` to mint a new one."""
        pool = node.session_name or ""
        head = self.leases.pool_head(chunk_id, pool)
        if head is None:
            return None  # an empty pool — this member mints the head
        breach = self._rotation_breach(head, node, spawn_cwd)
        if breach is None:
            return head.session_id
        _log.info(
            "rotating session pool",
            chunk_id=chunk_id,
            session_pool=pool,
            breached=breach,
            old_session_id=head.session_id,
        )
        return None

    def _rotation_breach(self, head: PoolHead, node: NodeConfig, spawn_cwd: str | None) -> str | None:
        """Why this pool head must not be resumed, or ``None`` when it may be (issue #144).

        A head is resumed only while every *readable* threshold is under bound and its stamped
        model still matches the resolved one. An unreadable signal is *not measured* and never
        a breach."""
        # Model drift first: the one check that needs no telemetry, and an edited declaration
        # should rotate regardless of how much context the old head accumulated.
        resolved = self.harness.resolve_model(node.session_model) if node.session_model else None
        if resolved is not None and head.resolved_model is not None and head.resolved_model != resolved:
            return "model-drift"

        rotate = node.session_rotate
        if rotate is None:
            return None  # the declaration bounds nothing

        if rotate.max_context_tokens is not None and self.transcripts is not None:
            # The transcript, never the usage facts: only it records per-turn prompt sizes, and
            # a usage row's cumulative figure is not this quantity (`Record.context_tokens`).
            tokens = self.transcripts.context_tokens(head.session_id, spawn_cwd=spawn_cwd)
            if tokens is not None and tokens > rotate.max_context_tokens:
                return "max_context_tokens"

        # A count is never an unknown — it is the number of rows that exist.
        if (
            rotate.max_invocations is not None
            and self.leases.session_invocation_count(head.session_id) > rotate.max_invocations
        ):
            return "max_invocations"

        if rotate.max_transcript_bytes is not None and self.transcripts is not None:
            # `size_bytes` returns `None` for an unreadable transcript — treated as unknown,
            # never a zero that would make the threshold silently inert.
            size = self.transcripts.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
            if size is not None and size > rotate.max_transcript_bytes:
                return "max_transcript_bytes"

        return None
