/** Hub stream vocabulary, independent of the generated transport types. */
export const HubEventKind = {
  CHUNK_CHANGED: 'chunk-changed', QUESTION_ASKED: 'question-asked', QUESTION_ANSWERED: 'question-answered',
  DECISION_OPENED: 'decision-opened', DECISION_RESOLVED: 'decision-resolved', QUEUE_CHANGED: 'queue-changed',
  RUNNER_CHANGED: 'runner-changed', HOST_CHANGED: 'host-changed', FLOW_CHANGED: 'flow-changed', EVENT_LOGGED: 'event-logged',
} as const;
export type HubEventKind = typeof HubEventKind[keyof typeof HubEventKind];

/** Known event kinds are distinct from an unforeseen wire name. */
export type HubEventType = HubEventKind | { readonly kind: 'unknown'; readonly raw: string };

export function hubEventTypeName(type: HubEventType): string {
  return typeof type === 'string' ? type : type.raw;
}

/** Facts used by live invalidation and Activity, with open string vocabularies. */
export interface HubEventData {
  /** Original payload of an unfamiliar frame, retained for diagnostics. */
  readonly raw?: unknown;
  readonly key?: string | null;
  readonly chunk_id?: string | null;
  readonly runner_id?: string | null;
  readonly runner_name?: string | null;
  readonly host_id?: string | null;
  readonly host_name?: string | null;
  readonly question_id?: string | null;
  readonly decision_id?: string | null;
  readonly flow_id?: string | null;
  readonly submission_id?: string | null;
  readonly project_id?: string | null;
  readonly graph_id?: string | null;
  readonly status?: string | null;
  readonly prev_status?: string | null;
  readonly node?: string | null;
  readonly prev_node?: string | null;
  readonly cause?: string | null;
  readonly kind?: string | null;
  readonly by?: string | null;
  readonly reason?: string | null;
  readonly severity?: string | null;
  readonly outcome?: string | null;
  readonly terminal?: boolean;
}

/** A durable activity row and a live frame share this payload contract. */
export interface ActivityFact {
  readonly type: HubEventType;
  readonly data: HubEventData;
  readonly key?: string;
  readonly at: number;
}
