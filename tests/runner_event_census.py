"""The write-protocol census (D5, blizzard#317 Phase 3) — every write-only member
:class:`~blizzard.runner.stores.IWriteRunnerStore` requires, whether declared on
its own class body or on a concept Protocol it inherits (e.g.
:class:`~blizzard.runner.domain.leases.IWriteLeaseRecordRepository`, blizzard#410), mapped to
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

#: The elapsed-time-derived samplers' and beats' shared reason (D7) — staleness bound is
#: `polling.ts`'s own to state (`bzh:one-prose-home`), not restated here.
_ELAPSED_TIME_DERIVED = (
    "elapsed-time-derived state (D7): eventing it would restore the request rate this "
    "change exists to remove, so the client re-derives it from elapsed time against an "
    "anchor timestamp the panel's own poll backstop refreshes, not continuously live."
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
    "record_graph_artifacts": Silent(
        "Spawner._mint (runner/loop/spawn.py) — the pinned mint's graph-scoped declarations, "
        "read only by the worker's own scoped artifact verb; no panel-facing kind represents it."
    ),
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
        "reason='escalated' additionally publishes escalation-changed(opened) at the same site. "
        "When `close()` is given an operational `event` (a retry or an exhausted-retries "
        "escalation), the write also buffers it to outbound_buffer, additionally announced as "
        "fact-changed(kind='event.recorded') at the same site (blizzard#317 review round 4, F1 — "
        "was buffered with no fact-changed announcement, the same defect class as "
        "record_local_pause/record_usage/record_context_sample/record_external_usage_attempt "
        "below).",
    ),
    "record_resume_intent": Silent(_INTERNAL_BOOKKEEPING + " (restart-resume marking)"),
    "record_resume_clear": Silent(_INTERNAL_BOOKKEEPING + " (restart-resume marking)"),
    "record_session_end": Silent(_INTERNAL_BOOKKEEPING + " (crash-recovery's declared-done fact)"),
    "record_lease_token": Silent(_INTERNAL_BOOKKEEPING + " (capability-token hash)"),
    "record_session_preamble": Silent(_INTERNAL_BOOKKEEPING + " (prompt-fingerprint cache)"),
    "record_nudge_fired": Silent(_INTERNAL_BOOKKEEPING + " (judgement's once-per-attempt nudge guard)"),
    "record_check_results": Silent(_INTERNAL_BOOKKEEPING + " (judgement's checks-at-exit rows)"),
    "record_checks_ran": Silent(_INTERNAL_BOOKKEEPING + " (judgement's checks-ran marker)"),
    "record_elicitation_launch": Silent(_INTERNAL_BOOKKEEPING + " (the detached elicitation's in-flight record)"),
    "record_elicitation_started": Silent(_INTERNAL_BOOKKEEPING + " (the elicitation's pid, once Popen returns)"),
    "record_elicitation_relaunch": Silent(_INTERNAL_BOOKKEEPING + " (a lost elicitation's retry record)"),
    "clear_elicitation": Silent(_INTERNAL_BOOKKEEPING + " (retiring a collected or closed-out elicitation record)"),
    # --- asks ----------------------------------------------------------------
    "record_ask": Published(ASK_CHANGED, "POST /api/leases/{lease_id}/asks (runner/api/asks.py) — cause='asked'"),
    "record_park": Published(
        LEASE_CHANGED,
        "DormantSession.park_on_ask (runner/loop/dormant.py) — cause='dormant'. The ask itself "
        "is already visible from record_ask's own 'asked' frame, but this write separately flips "
        "LeaseActivity.state (domain/leases.py) to 'parked' via parked_lease_ids(), which GET "
        "/api/leases renders as the row's headline label — a real leases-rail transition, "
        "distinct from open_asks()'s own unaffected derivation.",
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
    "record_pause_park": Published(
        LEASE_CHANGED,
        "Attempt.park_paused (runner/loop/attempt.py) — cause='dormant', the same "
        "LeaseActivity.state flip record_park causes above, reached via the operator-pause path "
        "instead of an ask. The hub-sourced pause fact D7 already covers is a different render "
        "(the chunk-detail pause banner) — this frame is for the leases-rail state, which that "
        "one does not stale.",
    ),
    "record_pause_park_resume": Silent(
        "fires in two shapes: unpausing a lease that is also ask-parked clears only the pause "
        "half — parked_lease_ids() stays true (still ask-parked), so no read-surface state "
        "changes; unpausing any other lease calls DormantSession._wake first, whose own "
        "record_spawn already publishes lease-changed(cause='spawned') for the state flip — this "
        "write's own effect is already announced either way."
    ),
    "set_hub_paused": Silent(
        "mirrors the hub's pause brake locally; no kind in the vocabulary represents it — "
        "backstop-bounded staleness, `polling.ts`'s own claim to state (`bzh:one-prose-home`)."
    ),
    "record_local_pause": Published(
        FACT_CHANGED,
        "SpendCeiling.run and patch_runner (runner/loop/steps.py, runner/api/control.py) — kind is "
        "the write's own report_kind (runner.locally_paused/resumed). The runner's own pause brake "
        "(issue #43/#61b) is distinct from the hub-sourced pause fact D7 already covers via the "
        "chunk-detail backstop; this frame is for the fact-log row this write always buffers "
        "(blizzard#317 review round 4, F1 — was wrongly Silent, since the write's own outbound_buffer "
        "insert bypassed enqueue_outbound and so went unannounced).",
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
    "record_usage": Published(
        FACT_CHANGED,
        "UsageRecorder.record_sample (runner/loop/usage.py) — kind='usage.recorded'. D7 names the "
        "usage *sampler's own elapsed-time readout* as backstop-bounded, but this write also always "
        "buffers a fact-log row (except on an exact-replay idempotent no-op, which enqueues nothing "
        "to announce) — a different render this frame covers (blizzard#317 review round 4, F1 — was "
        "wrongly Silent under a reason that addressed only the sampler, not the fact-log row the "
        "same write also creates by inserting into outbound_buffer directly, bypassing "
        "enqueue_outbound).",
    ),
    "record_context_sample": Published(
        FACT_CHANGED,
        "ContextSample._sample (runner/loop/steps.py) — kind='event.recorded', only on a first "
        "crossing (report_kind is empty otherwise, and nothing is enqueued to announce then). D7's "
        "elapsed-time-derived reason covers the sampler's own cadence, not this occasional fact-log "
        "row (blizzard#317 review round 4, F1 — same class as record_usage above).",
    ),
    "record_external_usage_attempt": Published(
        FACT_CHANGED,
        "ExternalUsageSample._sample_one (runner/loop/steps.py), called once per declared "
        "subscription per tick — kind='external_subscription_usage.sampled', only when that "
        "declaration's sampler produced a sample (report_kind is empty otherwise, and nothing is "
        "enqueued to announce then). `slug` (blizzard#436 phase 2) keys the row to its own "
        "declaration, so one subscription's attempt never announces, or advances the cadence of, "
        "another's. D7's elapsed-time-derived reason covers the sampler's own "
        "cadence, not this occasional fact-log row (blizzard#317 review round 4, F1 — same class as "
        "record_usage above).",
    ),
    # --- operator config -------------------------------------------------------------
    "set_workspace_prompt": Silent("operator-set runtime config; no kind in the vocabulary represents it."),
    "clear_workspace_prompt": Silent("the same operator-set runtime config, removed; no kind represents it either."),
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
