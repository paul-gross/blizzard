/*
 * Public API of the `fleet` shared library.
 *
 * The fleet views, the SSE transport, the health read, and the generated API
 * clients live here once (D-097) so both the hub app and the runner app compose
 * them. The two generated clients are re-exported under namespaces because the
 * hub and runner SDKs share operation names (e.g. `healthApiHealthGet`).
 */

export { BoardShell } from './lib/board-shell/board-shell';

export {
  SseService,
  EVENT_SOURCE_FACTORY,
  backoffDelay,
  type EventSourceFactory,
  type SseBackoff,
  type SseHandle,
  type SseStatus,
} from './lib/sse/sse.service';

export { injectHubHealthQuery } from './lib/health/health.query';

export * as hubApi from './lib/api/hub';
export * as runnerApi from './lib/api/runner';
