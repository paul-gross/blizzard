/*
 * Public API of the `fleet` shared library.
 *
 * The fleet views, the SSE transport + live-update spine, the reads/mutations, and
 * the generated API clients live here once so both the hub app and the runner
 * app compose them. The two generated clients are re-exported under namespaces because
 * the hub and runner SDKs share operation names (e.g. `healthApiHealthGet`).
 */

export { BoardHeader } from './lib/board-header/board-header';
export { BrandMark } from './lib/design/brand-mark';
export { BoardShell } from './lib/board-shell/board-shell';
export { compactRef, ENTITY_DISPLAY, type EntityDisplay } from './lib/compact-ref';
export { formatCost, formatTokens } from './lib/cost-format';
export { LANES, STATUS_LANE, laneFor, type Lane } from './lib/chunk-lanes';
export type { BoardCard } from './lib/board-shell/board-shell';
export { ChunkDetailPanel } from './lib/chunk-detail/chunk-detail-panel';
export type { AnswerQuestionEvent, ResolveDecisionEvent } from './lib/chunk-detail/chunk-detail-panel';
export { ChunkDetail } from './lib/chunk-detail/chunk-detail';
export { EventLogPanel } from './lib/event-log/event-log-panel';
export { QueuePanel } from './lib/queue/queue-panel';
export { RunnerPanel } from './lib/runners/runner-panel';
export { QuestionsPanel } from './lib/questions/questions-panel';

export {
  SseService,
  EVENT_SOURCE_FACTORY,
  backoffDelay,
  withLastEventId,
  type EventSourceFactory,
  type SseBackoff,
  type SseConnectOptions,
  type SseEvent,
  type SseHandle,
  type SseStatus,
} from './lib/sse/sse.service';
export { FleetLiveUpdates, HUB_EVENT_STREAM_URL, HUB_EVENT_TYPES, type LoggedEvent } from './lib/sse/fleet-live';

export { injectHubHealthQuery } from './lib/health/health.query';
export { injectHubChunksQuery } from './lib/chunks/chunks.query';
export { injectHubChunkDetailQuery } from './lib/chunks/chunk-detail.query';
export { injectAnswerQuestionMutation, injectResolveDecisionMutation } from './lib/chunks/human.mutations';
export type { AnswerVars, ResolveVars } from './lib/chunks/human.mutations';
export { injectDetachChunkMutation } from './lib/chunks/detach.mutations';
export type { DetachVars } from './lib/chunks/detach.mutations';

export { injectPromoteChunkMutation } from './lib/chunks/promote.mutations';
export type { PromoteVars } from './lib/chunks/promote.mutations';
export { injectChunkPauseMutation } from './lib/chunks/pause.mutations';
export type { ChunkPauseVars } from './lib/chunks/pause.mutations';
export { injectHubQueueQuery } from './lib/queue/queue.query';
export { injectReorderQueueMutation, injectGroupChunksMutation } from './lib/queue/queue.mutations';
export type { ReorderVars, GroupVars } from './lib/queue/queue.mutations';
export { injectHubRunnersQuery } from './lib/runners/runners.query';
export { injectRunnerPauseMutation } from './lib/runners/runners.mutations';
export type { RunnerPauseVars } from './lib/runners/runners.mutations';
export { injectHubQuestionsQuery } from './lib/questions/questions.query';
export { injectHubFleetSpendQuery } from './lib/fleet-spend/fleet-spend.query';

export {
  hubHealthKey,
  hubChunksKey,
  hubQueueKey,
  hubRunnersKey,
  hubQuestionsKey,
  hubChunkKey,
} from './lib/query-keys';

export type {
  ChunkSummary,
  ChunkStatus,
  ChunkDetail as ChunkDetailModel,
  TransitionView,
  ArtifactView,
  DecisionView,
  QuestionView,
  EscalationView,
  QueuePeekEntry,
  RunnerView,
  ChunkUsageTotalView,
  ChunkUsageView,
  FleetSpendView,
} from './lib/api/hub';

export * as hubApi from './lib/api/hub';
export * as runnerApi from './lib/api/runner';

/*
 * The runner client instance itself. The generated `index.ts` re-exports the SDK
 * functions and types but not the client, so a consumer outside this library has no
 * handle to configure its transport or stub it in a test. `local-panel` needs both.
 */
export { client as runnerClient } from './lib/api/runner/client.gen';
