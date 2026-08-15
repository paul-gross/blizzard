"""The write-protocol census (D5, blizzard#317 Phase 3) — every member
:class:`~blizzard.runner.store.repository.IWriteRunnerStore` declares **itself**, mapped to
either the event kind its mutation publishes (:class:`Published`) or a stated reason it
publishes nothing (:class:`Silent`). Exhaustiveness is carried by
``tests/test_runner_write_protocol_census.py``, this module's only reader — which is also
why it lives under ``tests/``, not ``src/``: no runtime importer."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.runner.events.broker import (
    ASK_CHANGED,
    ENVIRONMENT_CHANGED,
    ESCALATION_CHANGED,
    FACT_CHANGED,
    LEASE_CHANGED,
    TAKEOVER_CHANGED,
)


@dataclass(frozen=True)
class Published:
    """This write causes ``kind`` to publish, from ``where``."""

    kind: str
    where: str


@dataclass(frozen=True)
class Silent:
    """This write publishes nothing, for the stated ``reason``."""

    reason: str


Disposition = Published | Silent

#: The elapsed-time-derived samplers' and beats' shared reason (D7).
_ELAPSED_TIME_DERIVED = (
    "elapsed-time-derived state (D7): eventing it would restore the request rate this "
    "change exists to remove, and a client already re-derives it from elapsed time rather "
    "than from a cause a frame could carry."
)

#: The transcript lane's shared reason — it keeps its own poll (the falsified-claims table's one exception).
_TRANSCRIPT_LANE_POLLS = (
    "the transcript lane keeps its own poll (`transcript.query.ts`) rather than joining "
    "this stream; no kind in the vocabulary represents a transcript-lane write."
)

#: The reason every internal capability/auth/bookkeeping write gives — never rendered by any
#: read surface the panel calls, so no kind announces it.
_INTERNAL_BOOKKEEPING = (
    "internal bookkeeping with no client-facing read surface; no kind in the vocabulary represents it."
)

#: The full census over ``IWriteRunnerStore``'s own-declared members (D5) — keyed by the
#: method names ``tests/test_runner_write_protocol_census.py`` introspects at runtime.
WRITE_PROTOCOL_CENSUS: dict[str, Disposition] = {
    # --- lease lifecycle ---------------------------------------------------
    "record_lease": Published(LEASE_CHANGED, "Spawner._mint (runner/loop/spawn.py) — cause='created'"),
    "record_spawn": Published(
        LEASE_CHANGED,
        "Spawner.spawn (runner/loop/spawn.py) and DormantSession._wake (runner/loop/dormant.py) "
        "— cause='spawned', once the pid is durable; the 'created' mint alone leaves the "
        "spawning->running flip unannounced, since the lease is already visible but not yet live.",
    ),
    "record_closure": Published(
        LEASE_CHANGED,
        "Attempt.close (runner/loop/attempt.py) — cause=the closure reason itself, which IS "
        "the LeaseChangeCause vocabulary (transitioned/reaped/failed/escalated/parked/released); "
        "reason='escalated' additionally publishes escalation-changed(opened) at the same site.",
    ),
    "record_resume_intent": Silent(_INTERNAL_BOOKKEEPING + " (restart-resume marking)"),
    "record_resume_clear": Silent(_INTERNAL_BOOKKEEPING + " (restart-resume marking)"),
    "record_session_end": Silent(_INTERNAL_BOOKKEEPING + " (crash-recovery's declared-done fact)"),
    "record_lease_token": Silent(_INTERNAL_BOOKKEEPING + " (capability-token hash)"),
    "record_session_preamble": Silent(_INTERNAL_BOOKKEEPING + " (prompt-fingerprint cache)"),
    "record_nudge_fired": Silent(_INTERNAL_BOOKKEEPING + " (judgement's once-per-attempt nudge guard)"),
    "record_check_results": Silent(_INTERNAL_BOOKKEEPING + " (judgement's checks-at-exit rows)"),
    "record_checks_ran": Silent(_INTERNAL_BOOKKEEPING + " (judgement's checks-ran marker)"),
    # --- asks ----------------------------------------------------------------
    "record_ask": Published(ASK_CHANGED, "POST /api/leases/{lease_id}/asks (runner/api/asks.py) — cause='asked'"),
    "record_park": Silent(
        "the ask is already visible from its own 'asked' frame (record_ask); open_asks()'s "
        "derivation does not key on park/forward state, so this write moves nothing a read "
        "surface renders differently."
    ),
    "record_park_resume": Published(
        ASK_CHANGED,
        "DormantSession.on_answer (runner/loop/dormant.py) — cause='answered', the answer that "
        "actually resumed the session. The same method is also called from Attempt.abandon "
        "(runner/loop/attempt.py) to retire a stranded park with no answer — silent there, since "
        "the ask was not answered and that lease's own lease-changed(released) frame already "
        "prompts a re-read.",
    ),
    # --- operator pause (local + hub-mirrored) --------------------------------
    "record_pause_park": Silent(
        "the pause fact the panel renders is hub-sourced (D7 — `runner/api/chunk_detail.py`'s "
        "proxy); this local pause-park mirror carries no separate client-facing kind."
    ),
    "record_pause_park_resume": Silent("same as record_pause_park — the hub-sourced pause fact, not this mirror."),
    "set_hub_paused": Silent("mirrors the hub's pause brake locally; no kind in the vocabulary represents it."),
    "record_local_pause": Silent(
        "the runner's own pause brake (issue #43/#61b); no kind in the vocabulary represents it — "
        "distinct from the hub-sourced pause fact D7 already covers via the chunk-detail backstop."
    ),
    # --- escalations -----------------------------------------------------------
    "record_escalation_closure": Published(
        ESCALATION_CHANGED, "Pull._reconcile_escalations (runner/loop/steps.py) — cause='closed'"
    ),
    "record_requeue": Silent(
        "clears only the internal pending-requeue mark that gates FILL's interrupted-claim "
        "resume (`pending_requeue_chunk_ids`); the escalation itself stays open, per "
        "EscalationRecord's own derivation, until the retry's lease mint supersedes it — which "
        "Spawner's own lease-changed(created) frame announces."
    ),
    # --- takeovers ---------------------------------------------------------------
    "record_takeover": Published(TAKEOVER_CHANGED, "TakeoverService.open (runner/domain/takeover.py) — cause='opened'"),
    "record_takeover_end": Published(
        TAKEOVER_CHANGED,
        "TakeoverService.close (runner/domain/takeover.py) and Pull._reconcile_takeovers "
        "(runner/loop/steps.py) — both cause='closed', the CLI's own end and the hub-terminal "
        "supersession.",
    ),
    # --- environments --------------------------------------------------------------
    "record_binding": Published(ENVIRONMENT_CHANGED, "ReadyQueue._bind (runner/loop/claim.py) — cause='bound'"),
    "record_release": Published(
        ENVIRONMENT_CHANGED,
        "EnvironmentRelease.release_chunk/release_binding (runner/loop/env_release.py) — cause='released'",
    ),
    # --- outbound facts --------------------------------------------------------------
    "enqueue_outbound": Published(
        FACT_CHANGED,
        "OutboundFacts._enqueue (runner/loop/outbound.py), and TakeoverService.open's own fence-"
        "bump enqueue (runner/domain/takeover.py) — every hub-bound fact enqueued.",
    ),
    "ack_outbound": Published(
        FACT_CHANGED,
        "OutboundDrain._ack (runner/loop/drain.py) — the same seq re-announced. "
        "FactChangedPayload carries no acked state itself (D6), but the fact log's own ✓/· "
        "flush marker reads `acked_at` off the row this re-read fetches, so leaving this "
        "silent stales that marker until the next backstop poll.",
    ),
    # --- liveness/usage/context — the elapsed-time-derived samplers (D7) ------------
    "record_daemon_liveness": Silent(_ELAPSED_TIME_DERIVED + " (the daemon's own tick beat)"),
    "record_heartbeat": Silent(_ELAPSED_TIME_DERIVED + " (a worker's tool-call beat, named explicitly in D7)"),
    "record_usage": Silent(_ELAPSED_TIME_DERIVED + " (the usage sampler, named explicitly in D7)"),
    "record_context_sample": Silent(_ELAPSED_TIME_DERIVED + " (the context sampler, named explicitly in D7)"),
    "record_external_usage_attempt": Silent(_ELAPSED_TIME_DERIVED + " (the external-subscription-usage sampler)"),
    # --- operator config -------------------------------------------------------------
    "set_workspace_prompt": Silent("operator-set runtime config; no kind in the vocabulary represents it."),
    "set_route_token": Silent(_INTERNAL_BOOKKEEPING + " (won-claim capability token)"),
    # --- worker-submitted artifacts ---------------------------------------------------
    "record_attachment": Silent(
        "no kind represents a worker-submitted artifact; the chunk-detail view the panel reads "
        "is hub-sourced from the completion submission, not this runner-local staging fact."
    ),
    "record_git_commit_declaration": Silent("same as record_attachment — worker-local staging, no matching kind."),
    # --- the transcript lane — its own poll, no kind in this vocabulary ----------------
    "mark_transcript_record_truncated": Silent(_TRANSCRIPT_LANE_POLLS),
    "stop_transcript_segment_shipping": Silent(_TRANSCRIPT_LANE_POLLS),
    "mark_sidechain_dropped_warned": Silent(_TRANSCRIPT_LANE_POLLS),
    "record_transcript_deltas": Silent(_TRANSCRIPT_LANE_POLLS),
    "open_transcript_segment": Silent(_TRANSCRIPT_LANE_POLLS),
    "finalize_transcript_segment": Silent(_TRANSCRIPT_LANE_POLLS),
    "advance_transcript_cursor": Silent(_TRANSCRIPT_LANE_POLLS),
    "ack_transcript_outbound": Silent(_TRANSCRIPT_LANE_POLLS),
}
