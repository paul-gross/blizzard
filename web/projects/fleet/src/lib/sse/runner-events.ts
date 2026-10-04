/**
 * The runner's SSE event vocabulary and payload types, beside the hub's own in
 * {@link "./fleet-live"}. Every vocabulary and payload here is the runner's generated
 * client's; the golden corpus's runner scope at `contracts/sse/runner/` is the single
 * description both sides hold to.
 *
 * Frames are thin id-and-cause notifications; the runner app re-reads through the
 * runner's existing endpoints rather than the frame itself carrying a full view. This
 * module carries only the wire vocabulary — the event union type and payload map — so
 * the runner app can consume them. It builds no live-updates service or invalidation
 * registry of its own; `SseService` is imported unchanged.
 */

import {
  AskChangeCause,
  type AskChangedPayload,
  EnvironmentChangeCause,
  type EnvironmentChangedPayload,
  EscalationChangeCause,
  type EscalationChangedPayload,
  type FactChangedPayload,
  LeaseChangeCause,
  type LeaseChangedPayload,
  RunnerEventType,
  TakeoverChangeCause,
  type TakeoverChangedPayload,
} from '../api/runner';

export type {
  AskChangeCause,
  EnvironmentChangeCause,
  EscalationChangeCause,
  LeaseChangeCause,
  RunnerEventType,
  TakeoverChangeCause,
};

/** The runner's SSE stream endpoint (deliberately not in OpenAPI — native EventSource). */
export const RUNNER_EVENT_STREAM_URL = '/api/events/stream';

/** The named event types the runner broadcasts, in the generated {@link RunnerEventType}'s order. */
export const RUNNER_EVENT_TYPES: readonly RunnerEventType[] = Object.values(RunnerEventType);

/** What caused a `lease-changed` frame. */
export const LEASE_CHANGE_CAUSES: readonly LeaseChangeCause[] = Object.values(LeaseChangeCause);
/** What caused an `ask-changed` frame. */
export const ASK_CHANGE_CAUSES: readonly AskChangeCause[] = Object.values(AskChangeCause);
/** What caused an `escalation-changed` frame. */
export const ESCALATION_CHANGE_CAUSES: readonly EscalationChangeCause[] = Object.values(EscalationChangeCause);
/** What caused a `takeover-changed` frame. */
export const TAKEOVER_CHANGE_CAUSES: readonly TakeoverChangeCause[] = Object.values(TakeoverChangeCause);
/** What caused an `environment-changed` frame. */
export const ENVIRONMENT_CHANGE_CAUSES: readonly EnvironmentChangeCause[] = Object.values(EnvironmentChangeCause);

/**
 * Each runner frame type's generated payload. `escalation-changed` carries `lease_id`
 * only when the opening/closing lease is known; `fact-changed` carries `chunk_id` and
 * `lease_id` as `null`, not omitted, for a runner-wide fact that names neither.
 */
export interface RunnerEventPayloads {
  [RunnerEventType.LEASE_CHANGED]: LeaseChangedPayload;
  [RunnerEventType.ASK_CHANGED]: AskChangedPayload;
  [RunnerEventType.ESCALATION_CHANGED]: EscalationChangedPayload;
  [RunnerEventType.TAKEOVER_CHANGED]: TakeoverChangedPayload;
  [RunnerEventType.ENVIRONMENT_CHANGED]: EnvironmentChangedPayload;
  [RunnerEventType.FACT_CHANGED]: FactChangedPayload;
}

/** Any runner frame's payload — which member is fixed by the frame type it arrived under. */
export type RunnerEventPayload = RunnerEventPayloads[RunnerEventType];

/** A `lease-changed` frame's payload. */
export type LeaseChanged = LeaseChangedPayload;
/** An `ask-changed` frame's payload. */
export type AskChanged = AskChangedPayload;
/** An `escalation-changed` frame's payload. */
export type EscalationChanged = EscalationChangedPayload;
/** A `takeover-changed` frame's payload. */
export type TakeoverChanged = TakeoverChangedPayload;
/** An `environment-changed` frame's payload. */
export type EnvironmentChanged = EnvironmentChangedPayload;
/** A `fact-changed` frame's payload. */
export type FactChanged = FactChangedPayload;
