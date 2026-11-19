import { HubEventKind, type HubEventData } from './hub-events.domain';
import { type HubEventType } from '../api/hub';
import { openLookup } from '../core/open-lookup';

const kinds = openLookup<HubEventType, HubEventKind | null>({
  'chunk-changed': HubEventKind.CHUNK_CHANGED,
  'question-asked': HubEventKind.QUESTION_ASKED,
  'question-answered': HubEventKind.QUESTION_ANSWERED,
  'decision-opened': HubEventKind.DECISION_OPENED,
  'decision-resolved': HubEventKind.DECISION_RESOLVED,
  'queue-changed': HubEventKind.QUEUE_CHANGED,
  'runner-changed': HubEventKind.RUNNER_CHANGED,
  'host-changed': HubEventKind.HOST_CHANGED,
  'flow-changed': HubEventKind.FLOW_CHANGED,
  'event-logged': HubEventKind.EVENT_LOGGED,
}, () => null);
/** Every known wire name is subscribed; the table is exhaustive over the generated boundary. */
export const HUB_EVENT_TYPES: readonly string[] = kinds.keys;
const fields = ['key', 'chunk_id', 'runner_id', 'runner_name', 'host_id', 'host_name', 'question_id', 'decision_id',
  'flow_id', 'submission_id', 'project_id', 'graph_id', 'status', 'prev_status', 'node', 'prev_node', 'cause', 'kind',
  'by', 'reason', 'severity', 'outcome'] as const;

/** Translate once at ingress; unknown frames retain their vocabulary and payload for diagnostics. */
export function translateHubEvent(type: string, raw: unknown): { type: string; data: HubEventData } | null {
  const kind = kinds(type);
  if (kind === null) return { type, data: { raw } };
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const source = raw as Record<string, unknown>;
  const data: Record<string, string | boolean | null> = {};
  for (const field of fields) {
    const value = source[field];
    if (typeof value === 'string' || value === null) data[field] = value;
  }
  if (typeof source['terminal'] === 'boolean') data['terminal'] = source['terminal'];
  return { type: kind, data: data as HubEventData };
}
