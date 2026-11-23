import {
  type ChunkChangedPayload,
  type DecisionOpenedPayload,
  type DecisionResolvedPayload,
  type EventLoggedPayload,
  type FlowChangedPayload,
  type HostChangedPayload,
  HubEventType,
  type QuestionAnsweredPayload,
  type QuestionAskedPayload,
  type QueueChangedPayload,
  type RunnerChangeKind,
  type RunnerChangedPayload,
} from '../../api/hub';

export type { RunnerChangeKind };

/** The hub's SSE stream endpoint (deliberately not in OpenAPI — native EventSource). */
export const HUB_EVENT_STREAM_URL = '/api/events/stream';

/** The stream's URL for the project `project` names: `?project=` narrows the frames to that project's, `null` reads the
 * whole tenant. */
export function hubEventStreamUrl(project: string | null): string {
  return project === null ? HUB_EVENT_STREAM_URL : `${HUB_EVENT_STREAM_URL}?project=${encodeURIComponent(project)}`;
}

/** The named event types the hub broadcasts, in the generated {@link HubEventType}'s order. */
export const HUB_EVENT_TYPES: readonly HubEventType[] = Object.values(HubEventType);

/**
 * Each hub frame type's generated payload. Optional fields are present-when-meaningful
 * (a chunk that has never transitioned carries no `prev_node`); the hub's event
 * broker owns `key`.
 */
export interface HubEventPayloads {
  [HubEventType.CHUNK_CHANGED]: ChunkChangedPayload;
  [HubEventType.QUESTION_ASKED]: QuestionAskedPayload;
  [HubEventType.QUESTION_ANSWERED]: QuestionAnsweredPayload;
  [HubEventType.DECISION_OPENED]: DecisionOpenedPayload;
  [HubEventType.DECISION_RESOLVED]: DecisionResolvedPayload;
  [HubEventType.QUEUE_CHANGED]: QueueChangedPayload;
  [HubEventType.RUNNER_CHANGED]: RunnerChangedPayload;
  [HubEventType.HOST_CHANGED]: HostChangedPayload;
  [HubEventType.FLOW_CHANGED]: FlowChangedPayload;
  [HubEventType.EVENT_LOGGED]: EventLoggedPayload;
}

/** Any hub frame's payload — which member is fixed by the frame type it arrived under. */
export type HubEventPayload = HubEventPayloads[HubEventType];

/** A `chunk-changed` frame's payload. */
export type ChunkChanged = ChunkChangedPayload;
/** A `question-asked`/`question-answered` frame's payload. */
export type QuestionEvent = QuestionAskedPayload | QuestionAnsweredPayload;
/** A `decision-opened`/`decision-resolved` frame's payload. */
export type DecisionEvent = DecisionOpenedPayload | DecisionResolvedPayload;
/** A `runner-changed` frame's payload. */
export type RunnerEvent = RunnerChangedPayload;
/** A `host-changed` frame's payload. */
export type HostEvent = HostChangedPayload;
/** A `flow-changed` frame's payload. */
export type FlowEvent = FlowChangedPayload;
/** An `event-logged` frame's payload. */
export type EventLoggedEvent = EventLoggedPayload;
/** The fact-identity stamp every keyed frame carries ({@link HubEventPayloads}). */
export type KeyedEvent = Pick<ChunkChangedPayload, 'key'>;
