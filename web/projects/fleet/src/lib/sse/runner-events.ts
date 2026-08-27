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

/** What caused a `lease-changed` frame — the split is `sse_runner.py`'s own to state. */
export const LEASE_CHANGE_CAUSES = [
  'created',
  'spawned',
  'dormant',
  'transitioned',
  'reaped',
  'failed',
  'escalated',
  'parked',
  'released',
  'preempted',
] as const;
export type LeaseChangeCause = (typeof LEASE_CHANGE_CAUSES)[number];
/** What caused an `ask-changed` frame. */
export const ASK_CHANGE_CAUSES = ['asked', 'answered'] as const;
export type AskChangeCause = (typeof ASK_CHANGE_CAUSES)[number];
/** What caused an `escalation-changed` frame. */
export const ESCALATION_CHANGE_CAUSES = ['opened', 'closed'] as const;
export type EscalationChangeCause = (typeof ESCALATION_CHANGE_CAUSES)[number];
/** What caused a `takeover-changed` frame. */
export const TAKEOVER_CHANGE_CAUSES = ['opened', 'closed'] as const;
export type TakeoverChangeCause = (typeof TAKEOVER_CHANGE_CAUSES)[number];
/** What caused an `environment-changed` frame. */
export const ENVIRONMENT_CHANGE_CAUSES = ['bound', 'released'] as const;
export type EnvironmentChangeCause = (typeof ENVIRONMENT_CHANGE_CAUSES)[number];

/** A `lease-changed` frame's payload. `cause` is typed `string`, not
 * {@link LeaseChangeCause}, for the same reason `fleet-live.ts`'s `RunnerEvent.kind`
 * is: {@link RunnerEventPayload} intersects every payload interface here, and each
 * carries its own disjoint `cause` literal union — intersecting those collapses the
 * field (and, with it, the whole type) to `never`. */
export interface LeaseChanged {
  lease_id: string;
  chunk_id: string;
  cause: string;
}
/** An `ask-changed` frame's payload — a worker's question recorded, or its answer
 * landing (the park resume the answer drives). `cause` typed `string` — see
 * {@link LeaseChanged}. */
export interface AskChanged {
  lease_id: string;
  chunk_id: string;
  question_id: string;
  cause: string;
}
/** An `escalation-changed` frame's payload — opened at an exhausted retry budget, or
 * closed by supersession. `lease_id` is present only when the closing/opening lease is
 * known. `cause` typed `string` — see {@link LeaseChanged}. */
export interface EscalationChanged {
  chunk_id: string;
  cause: string;
  lease_id?: string;
}
/** A `takeover-changed` frame's payload. `cause` typed `string` — see
 * {@link LeaseChanged}. */
export interface TakeoverChanged {
  chunk_id: string;
  takeover_id: string;
  cause: string;
}
/** An `environment-changed` frame's payload. `cause` typed `string` — see
 * {@link LeaseChanged}. */
export interface EnvironmentChanged {
  chunk_id: string;
  environment_id: string;
  cause: string;
}
/** A `fact-changed` frame's payload — a hub-bound fact was enqueued onto the outbound
 * buffer. `chunk_id`/`lease_id` are always present, `null` rather than omitted, for a
 * runner-wide fact (e.g. a chunk-less `event.recorded`) that names neither. */
export interface FactChanged {
  seq: number;
  kind: string;
  chunk_id: string | null;
  lease_id: string | null;
}
export type RunnerEventPayload = Partial<
  LeaseChanged & AskChanged & EscalationChanged & TakeoverChanged & EnvironmentChanged & FactChanged
>;
