/*
 * Eager-shell entry point of the `fleet` shared library for the runner app (`fleet/runner-shell`):
 * `fleet/shell`'s counterpart, named re-exports of exactly what the runner's eager shell statically
 * reaches beyond it (`bzh:frontend-eager-shell-entry`, gated by `web:bundle-composition`). It is a
 * second file because esbuild keeps every decorated or side-effectful module a re-export names,
 * used or not, so anything only the runner needs would otherwise grow the hub's initial chunk.
 */

export { type StatCell } from './lib/shell/board-header/board-header';
export { SseService, backoffDelay, type SseStatus } from './lib/sse/sse.service';
export { INVALIDATION_COALESCE_WINDOW_MS } from './lib/sse/fleet-live';
export { LiveInvalidationSpine } from './lib/sse/live-invalidation-spine';
export {
  RUNNER_EVENT_STREAM_URL,
  RUNNER_EVENT_TYPES,
  type RunnerEventPayload,
  type RunnerEventPayloads,
  type RunnerEventType,
} from './lib/sse/runner-events';

export { KitGlyph } from './lib/kit/kit-glyph';
export { type SlotBarSlot } from './lib/kit/kit-slot-bar';
export { type Tone } from './lib/kit/tone';
export { compactRef } from './lib/core/compact-ref';
export { openLookup, unlisted } from './lib/core/open-lookup';
export { RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './lib/core/polling/polling';
export { chunkDetailKey, runnerFlagsKey } from './lib/core/query-keys';

export * as runnerApi from './lib/api/runner';
export { client as runnerClient } from './lib/api/runner/client.gen';
