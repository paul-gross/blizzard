"""Which prior session a node-entry spawn resumes, and the session stamps it runs under."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.graph import SessionMode
from blizzard.runner.harness.adapter import IHarnessAdapter
from blizzard.runner.harness.transcript import IHarnessTranscriptSource
from blizzard.runner.store.repository import IReadRunnerStore, LeaseRecord, PoolHead
from blizzard.wire.envelope import NodeConfig

_log = get_logger("blizzard.runner.loop")


@dataclass(frozen=True)
class SessionResolver:
    """Resolves a spawn's session identity against the store's own session history."""

    store: IReadRunnerStore
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
        return self.store.latest_session_id(chunk_id, node.session_source)

    def resumed_lease(self, resume_from: str | None) -> LeaseRecord | None:
        """The newest recorded lease of the session this spawn resumes, or ``None`` for a fresh
        mint — the one read (blizzard#340) serving both the stamps and the resume notice's
        prior node, so no caller re-derives it from the store or the transcript."""
        return self.store.lease_for_session(resume_from) if resume_from is not None else None

    def session_stamps(
        self, node: NodeConfig, resume_from: str | None, prior: LeaseRecord | None
    ) -> tuple[str | None, str | None, str | None]:
        """The (model, effort, compaction_window) this spawn runs under, and stamps (#144, blizzard#343).

        **The stamp describes the session, not the preference.** A spawn that *resumes* inherits
        all three from ``prior`` — the resumed session's own recorded lease, from
        :meth:`resumed_lease` — and an inherited ``None`` stays *unknown*."""
        if resume_from is not None:
            if prior is None:
                return (None, None, None)
            return (prior.resolved_model, prior.resolved_effort, prior.resolved_compaction_window)
        model = self.harness.resolve_model(node.session_model)
        return (
            model,
            self.harness.resolve_effort(node.session_effort),
            self.harness.resolve_compaction_window(node.session_compaction_window),
        )

    def _pool_head(self, chunk_id: str, node: NodeConfig, spawn_cwd: str | None) -> str | None:
        """The named pool's head if it is still resumable, else ``None`` to mint a new one."""
        pool = node.session_name or ""
        head = self.store.pool_head(chunk_id, pool)
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
            and self.store.session_invocation_count(head.session_id) > rotate.max_invocations
        ):
            return "max_invocations"

        if rotate.max_transcript_bytes is not None and self.transcripts is not None:
            # `size_bytes` returns `None` for an unreadable transcript — treated as unknown,
            # never a zero that would make the threshold silently inert.
            size = self.transcripts.size_bytes(head.session_id, spawn_cwd=spawn_cwd)
            if size is not None and size > rotate.max_transcript_bytes:
                return "max_transcript_bytes"

        return None
