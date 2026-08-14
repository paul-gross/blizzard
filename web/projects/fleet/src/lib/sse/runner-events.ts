/**
 * The runner's SSE event vocabulary and payload interfaces (blizzard#317 Phase 2), beside
 * the hub's own in {@link "./fleet-live"}. Mirrors ``blizzard.runner.events.broker``'s
 * event-type constants and ``blizzard.wire.sse_runner``'s payload models — the golden
 * corpus's runner scope at `contracts/sse/runner/` is the single description both sides
 * hold to.
 *
 * D6: frames are thin id-and-cause notifications; `local-panel` re-reads through the
 * runner's existing endpoints rather than the frame itself carrying a full view. This
 * module carries only the wire vocabulary — the event union type and payload
 * interfaces — so `local-panel` can consume them (Phase 4). It builds no live-updates
 * service or invalidation registry of its own; `SseService` is imported unchanged.
 */

/** The runner's SSE stream endpoint (deliberately not in OpenAPI — native EventSource). */
export const RUNNER_EVENT_STREAM_URL = '/api/events/stream';

/** The named event types the runner broadcasts. */
export const RUNNER_EVENT_TYPES = [
  'lease-changed',
  'ask-changed',
  'escalation-changed',
  'takeover-changed',
  'environment-changed',
  'fact-changed',
] as const;

/** One of the named event types the runner broadcasts ({@link RUNNER_EVENT_TYPES}). */
export type RunnerEventType = (typeof RUNNER_EVENT_TYPES)[number];

/** What caused a `lease-changed` frame — `created` on mint, the rest mirroring the
 * runner store's own closure vocabulary (`ClosedLeaseRecord.reason`). */
export type LeaseChangeCause = 'created' | 'transitioned' | 'reaped' | 'failed' | 'escalated' | 'parked' | 'released';
/** What caused an `ask-changed` frame. */
export type AskChangeCause = 'asked' | 'answered';
/** What caused an `escalation-changed` frame. */
export type EscalationChangeCause = 'opened' | 'closed';
/** What caused a `takeover-changed` frame. */
export type TakeoverChangeCause = 'opened' | 'closed';
/** What caused an `environment-changed` frame. */
export type EnvironmentChangeCause = 'bound' | 'released';

/** A `lease-changed` frame's payload. `key`/`node_name` are present-when-meaningful,
 * omitted rather than `null` when they do not apply. */
export interface LeaseChanged {
  lease_id: string;
  chunk_id: string;
  cause: LeaseChangeCause;
  node_name?: string;
}
/** An `ask-changed` frame's payload — a worker's question recorded, or its answer
 * landing (the park resume the answer drives). */
export interface AskChanged {
  lease_id: string;
  chunk_id: string;
  question_id: string;
  cause: AskChangeCause;
}
/** An `escalation-changed` frame's payload — opened at an exhausted retry budget, or
 * closed by supersession. `lease_id` is present only when the closing/opening lease is
 * known. */
export interface EscalationChanged {
  chunk_id: string;
  cause: EscalationChangeCause;
  lease_id?: string;
}
/** A `takeover-changed` frame's payload. */
export interface TakeoverChanged {
  chunk_id: string;
  takeover_id: string;
  cause: TakeoverChangeCause;
}
/** An `environment-changed` frame's payload. */
export interface EnvironmentChanged {
  chunk_id: string;
  environment_id: string;
  cause: EnvironmentChangeCause;
}
/** A `fact-changed` frame's payload — a hub-bound fact was enqueued onto the outbound
 * buffer. `chunk_id`/`lease_id` are always present, `null` rather than omitted, for a
 * runner-wide fact (e.g. a heartbeat) that names neither. */
export interface FactChanged {
  seq: number;
  kind: string;
  chunk_id: string | null;
  lease_id: string | null;
}
/** The fact-identity stamp a runner frame carries when one durable fact backs it —
 * mirrors the hub's own {@link "./fleet-live".KeyedEvent}. Omitted on a frame with no
 * such fact. */
export interface RunnerKeyedEvent {
  key?: string;
}

export type RunnerEventPayload = Partial<
  LeaseChanged & AskChanged & EscalationChanged & TakeoverChanged & EnvironmentChanged & FactChanged & RunnerKeyedEvent
>;
