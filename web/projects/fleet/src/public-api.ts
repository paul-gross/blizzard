/*
 * Public API of the `fleet` shared library.
 *
 * The fleet views, the SSE transport + live-update spine, the reads/mutations, and
 * the generated API clients live here once so both the hub app and the runner
 * app compose them. The two generated clients are re-exported under namespaces because
 * the hub and runner SDKs share operation names (e.g. `healthApiHealthGet`).
 *
 * This barrel (issue #82) is deliberately thin: every feature directory under `lib/`
 * owns its own `index.ts` sub-barrel — including the domain view types it re-exports
 * from the generated hub client — and is re-exported here one line each, so two
 * features landing in parallel touch different sub-barrels instead of colliding on
 * this file (`architecture/frontend-structure.md`'s disjoint-diffs mechanism). Only
 * what has no single feature owner — the query-key registry and the generated-client
 * surface itself — stays exported directly at root.
 */

export * from './lib/kit';
export * from './lib/auth';
export * from './lib/admin';
export * from './lib/design';
export * from './lib/format';
export * from './lib/board-header';
export * from './lib/board-shell';
export * from './lib/chunk-detail';
export * from './lib/event-log';
export * from './lib/events';
export * from './lib/queue';
export * from './lib/runners';
export * from './lib/questions';
export * from './lib/graphs';
export * from './lib/sse';
export * from './lib/health';
export * from './lib/fleet-spend';
export * from './lib/chunks';
export * from './lib/viewport';
export * from './lib/mobile-chrome';

export {
  hubHealthKey,
  hubChunksKey,
  hubQueueKey,
  hubRunnersKey,
  hubQuestionsKey,
  hubChunkKey,
  hubGraphsKey,
  hubGraphKey,
} from './lib/query-keys';

export * as hubApi from './lib/api/hub';
export * as runnerApi from './lib/api/runner';

/*
 * The client instances themselves. The generated `index.ts` re-exports the SDK
 * functions and types but not the client, so a consumer outside this library has no
 * handle to configure its transport or stub it in a test. `local-panel` needs the
 * runner one; the `hub` app needs the hub one (e.g. `graphs-page.spec.ts` stubs the
 * hub transport to settle the graph-detail query deterministically).
 */
export { client as runnerClient } from './lib/api/runner/client.gen';
export { client as hubClient } from './lib/api/hub/client.gen';
