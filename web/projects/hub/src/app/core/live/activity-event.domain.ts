import { HubEventKind, type HubEventData, type HubEventType } from 'fleet/shell';

/** The durable Activity vocabulary; this policy does not control invalidation. */
const ACTIVITY_RUNNER_KINDS: ReadonlySet<string> = new Set(['paused', 'resumed', 'locally-paused', 'locally-resumed']);
const ACTIVITY_CHUNK_CAUSES: ReadonlySet<string> = new Set([
  'minted', 'promoted', 'demoted', 'grouped', 'claimed', 'node-completed', 'migrated', 'decision-submitted',
  'decision-resolved', 'question-asked', 'question-answered', 'escalated', 'requeued', 'restarted', 'detached',
  'released', 'paused', 'resumed', 'stopped', 'completed', 'hub-advanced', 'deleted',
]);

/** Only facts with durable Activity rows enter the live feed. */
export function isActivityEvent(type: HubEventType, data: HubEventData): boolean {
  if (type === HubEventKind.CHUNK_CHANGED) return ACTIVITY_CHUNK_CAUSES.has(data.cause ?? '');
  if (type === HubEventKind.RUNNER_CHANGED) return ACTIVITY_RUNNER_KINDS.has(data.kind ?? '');
  return type === HubEventKind.EVENT_LOGGED;
}

