export { HUB_EVENT_TYPES } from './hub-events.translate';

/** The hub's named event stream. */
export const HUB_EVENT_STREAM_URL = '/api/events/stream';
export function hubEventStreamUrl(project: string | null): string {
  return project === null ? HUB_EVENT_STREAM_URL : `${HUB_EVENT_STREAM_URL}?project=${encodeURIComponent(project)}`;
}
