import type { ActivityView } from 'fleet';
import { HubEventKind, openLookup, type ActivityFact, type HubEventType } from 'fleet/shell';

const activityTypes = openLookup<ActivityView['type'], HubEventType>({
  'chunk-changed': HubEventKind.CHUNK_CHANGED,
  'runner-changed': HubEventKind.RUNNER_CHANGED,
  'event-logged': HubEventKind.EVENT_LOGGED,
}, (raw) => ({ kind: 'unknown', raw }));

/** Translate a durable activity row into the same facts the live feed uses. */
export function translateActivityRow(row: ActivityView): ActivityFact {
  const { at, type, key, ...data } = row;
  const eventType = activityTypes(type);
  return { at: Date.parse(at), type: eventType, key, data: { ...data, key, ...(typeof eventType === 'string' ? {} : { raw: row }) } };
}
