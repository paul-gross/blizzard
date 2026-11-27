/* The `fleet/sse` entry point: the live-update transport and its event vocabulary. */

export {
  type ActivityFact,
  type HubEventData,
  HubEventKind,
  type HubEventType,
  hubEventTypeName,
} from '../lib/sse/hub-events.domain';
export { HUB_EVENT_TYPES, translateHubEvent } from '../lib/sse/hub-events.translate';
export { HUB_EVENT_STREAM_URL, hubEventStreamUrl } from '../lib/sse/hub-events';
export { INVALIDATION_COALESCE_WINDOW_MS, LiveInvalidationSpine } from '../lib/sse/live-invalidation-spine';
export { RUNNER_EVENT_STREAM_URL } from '../lib/sse/runner-events';
export type { RunnerStreamEvent } from '../lib/sse/runner-stream.domain';
export { RUNNER_EVENT_TYPES, translateRunnerStreamEvent } from '../lib/sse/runner-stream.translate';
export {
  backoffDelay,
  EVENT_SOURCE_FACTORY,
  type EventSourceFactory,
  FetchEventSource,
  type FleetEventSource,
  SseService,
  type SseStatus,
} from '../lib/sse/sse.service';
