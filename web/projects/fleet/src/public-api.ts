/*
 * Public API of the `fleet` shared library.
 *
 * The fleet views, the SSE transport + live-update spine, the reads/mutations, and
 * the generated API clients live here once so both the hub app and the runner
 * app compose them. The two generated clients are re-exported under namespaces because
 * the hub and runner SDKs share operation names (e.g. `healthApiHealthGet`).
 *
 * This barrel is deliberately thin: every feature directory under `lib/`
 * owns its own `index.ts` sub-barrel — including the domain view types it re-exports
 * from the generated hub client — and is re-exported here one line each, so two
 * features landing in parallel touch different sub-barrels instead of colliding on
 * this file (`architecture/frontend-structure/disjoint-diffs.md`). Only
 * what has no single feature owner — the query-key registry and the generated-client
 * surface itself — stays exported directly at root.
 */

export * from './lib/kit';
export * from './lib/shell/app-shell';
export * from './lib/chunk/chunk-page';
export * from './lib/core/design';
export * from './lib/core/format';
export * from './lib/core/when-display';
export * from './lib/core/now-signal';
export * from './lib/core/clipboard';
export * from './lib/shell/board-header';
export * from './lib/chunk/chunk-detail';
export * from './lib/chunk/chunk-artifacts-panel';
export * from './lib/chunk/chunk-issue-list';
export * from './lib/sse';
export * from './lib/core/mutation-pending';
export * from './lib/transcripts';
export * from './lib/core/url-selection';
export * from './lib/core/viewport';
export * from './lib/shell/mobile-chrome';

export {
  hubHealthKey,
  hubChunksKey,
  hubQueueKey,
  hubBacklogKey,
  hubRunnersKey,
  hubQuestionsKey,
  hubDecisionsKey,
  hubGardenProposalsKey,
  hubChunkKey,
  chunkDetailKey,
  chunkWorkItemsKey,
  hubGraphsKey,
  hubGraphKey,
  chunkTranscriptsKey,
  chunkTranscriptSegmentKey,
  type TranscriptPlane,
  hubActivityKey,
  hubAuthProvidersKey,
  hubBoardChunksKey,
  hubChunkCountsKey,
  hubConfigChangesKey,
  hubConfigHistoryKey,
  hubConfigKey,
  hubConfigListKey,
  hubConfigRecordKey,
  hubEventsKey,
  hubFindingKey,
  hubFindingPrefixKey,
  hubFindingsBucketKey,
  hubFindingsBucketPrefixKey,
  hubFindingsKey,
  hubFleetSpendKey,
  hubGardenProposalKey,
  hubMeKey,
  hubRoutineBaselinesKey,
  hubRoutineProposalCountsKey,
  hubRoutineProposalCountsPrefixKey,
  hubRoutineScopesKey,
  hubRoutineSweepsKey,
  hubRoutineTrendKey,
  hubRoutinesKey,
  hubRunDeltaKey,
  hubRunsKey,
  hubScopeRoutinesKey,
  hubScopesKey,
  hubUsersKey,
  hubWorkItemKey,
  hubWorkItemsKey,
} from './lib/core/query-keys';
export * from './lib/core/query-state';
export { LIVE_COVERED_POLL_BACKSTOP_MS, RUNNER_LIVE_COVERED_POLL_BACKSTOP_MS } from './lib/core/polling';

export * as hubApi from './lib/api/hub';
export * as runnerApi from './lib/api/runner';

/*
 * The client instances themselves. The generated `index.ts` re-exports the SDK
 * functions and types but not the client, so a consumer outside this library has no
 * handle to configure its transport or stub it in a test. the `runner` app needs the
 * runner one; the `hub` app needs the hub one (e.g. `graphs-page.spec.ts` stubs the
 * hub transport to settle the graph-detail query deterministically).
 */
export { client as runnerClient } from './lib/api/runner/client.gen';
export { client as hubClient } from './lib/api/hub/client.gen';
export type { Client } from './lib/api/hub/client';
export type { HubEventPayload } from './lib/sse/fleet-live';
export { formatClockTime, formatRefreshedAgo, formatWhen } from './lib/core/when';
export { completeCopy, deleteCopy, detachCopy } from './lib/chunk/chunk-detail/chunk-action-copy';
export { ChunkDelivery } from './lib/chunk/chunk-detail/chunk-delivery';
export type {
  RepositoryDocument,
  RepositoryPatchRequest,
  WorkSourceDocument,
  WorkSourcePatchRequest,
  ActivityView,
  AnswerResult,
  ConfigChangeView,
  ConfigChangesPage,
  FieldChangeView,
  RecordRefView,
  RepositorySummary,
  SecretView,
  WorkSourceSummary,
  BacklogPeekEntry,
  BacklogPeekResponse,
  ChunkCountsView,
  ChunkStatus,
  ChunkSummary,
  DecisionResolutionResponse,
  DecisionView,
  EventView,
  ExternalSubscriptionUsageWindowView,
  FindingDetailView,
  FindingFactView,
  FindingView,
  FleetSpendView,
  GardenProposalAcceptResponse,
  GardenProposalClosureView,
  GardenProposalCountsView,
  GardenProposalView,
  GardenSweepsView,
  GraphChoiceView,
  GraphNodeView,
  GraphSessionView,
  GraphSummaryView,
  GraphView,
  LandedRepoView,
  MeResponse,
  PauseView,
  PrView,
  ProviderSummary,
  QuestionView,
  QueuePeekEntry,
  QueuePeekResponse,
  RouteView,
  RoutineBaselineView,
  RoutineRunResponse,
  RoutineView,
  RunDeltaView,
  RunRowView,
  RunnerCapability,
  RunnerView,
  ScopeView,
  TrendView,
  UserView,
  WorkItemView,
  WorkRefView,
} from './lib/api/hub';
export {
  RecordKind,
  createRepositoryApiRepositoriesPost,
  createSecretApiSecretsPost,
  createWorkSourceApiWorkSourcesPost,
  enableRepositoryApiRepositoriesNameEnablePost,
  enableSecretApiSecretsNameEnablePost,
  enableWorkSourceApiWorkSourcesSourceEnablePost,
  patchRepositoryApiRepositoriesNamePatch,
  patchWorkSourceApiWorkSourcesSourcePatch,
  replaceSecretApiSecretsNameValuePut,
  retireRepositoryApiRepositoriesNameRetirePost,
  retireSecretApiSecretsNameRetirePost,
  retireWorkSourceApiWorkSourcesSourceRetirePost,
  getRepositoryApiRepositoriesNameGet,
  getSecretApiSecretsNameGet,
  getWorkSourceApiWorkSourcesSourceGet,
  listChangesApiConfigChangesGet,
  listRepositoriesApiRepositoriesGet,
  listSecretsApiSecretsGet,
  listWorkSourcesApiWorkSourcesGet,
  acceptGardenProposalApiGardenProposalsProposalIdAcceptPost,
  answerQuestionApiQuestionsQuestionIdAnswersPost,
  assignRoleApiUsersUserIdRolePost,
  chunkCountsApiChunkCountsGet,
  completeChunkApiChunksChunkIdCompletePost,
  confirmGoneFindingsApiFindingsConfirmGonePost,
  deleteChunkApiChunksChunkIdDelete,
  detachChunkApiChunksChunkIdDetachPost,
  editScopeApiScopesSlugPatch,
  enableGraphApiGraphsGraphIdEnablePost,
  enableRoutineApiRoutinesRoutineIdEnablePost,
  enableScopeApiScopesSlugEnablePost,
  fleetSpendApiSpendGet,
  getBacklogApiBacklogGet,
  getFindingApiFindingsFindingIdGet,
  getGraphApiGraphsGraphIdGet,
  getQueueApiQueueGet,
  getWorkItemApiWorkSourcesSourceItemsRefGet,
  healthApiHealthGet,
  listActivityApiActivityGet,
  listChunksApiChunksGet,
  listEventsApiEventsGet,
  listFindingsApiFindingsGet,
  listGardenProposalsApiGardenProposalsGet,
  listGraphsApiGraphsGet,
  listOpenQuestionsApiQuestionsGet,
  listProvidersApiAuthProvidersGet,
  listRoutineScopesApiRoutinesRoutineIdScopesGet,
  listRoutinesApiRoutinesGet,
  listRunnersApiRunnersGet,
  listRunsApiRunsGet,
  listScopeRoutinesApiScopesSlugRoutinesGet,
  listScopesApiScopesGet,
  listUsersApiUsersGet,
  logoutApiAuthLogoutPost,
  meApiMeGet,
  notAFindingFindingsApiFindingsNotAFindingPost,
  passGardenProposalApiGardenProposalsProposalIdPassPost,
  patchChunkApiChunksChunkIdPatch,
  pauseChunkApiChunksChunkIdPausePost,
  pauseRunnerApiRunnersRunnerIdPausePost,
  promoteChunkApiChunksChunkIdPromotePost,
  reopenFindingsApiFindingsReopenPost,
  repositionBacklogApiBacklogPositionPost,
  repositionQueueApiQueuePositionPost,
  resolveDecisionApiDecisionsDecisionIdResolutionsPost,
  resolveFindingsApiFindingsResolvePost,
  resumeChunkApiChunksChunkIdResumePost,
  resumeRunnerApiRunnersRunnerIdResumePost,
  retireGraphApiGraphsGraphIdRetirePost,
  retireRoutineApiRoutinesRoutineIdRetirePost,
  retireScopeApiScopesSlugRetirePost,
  routineBaselinesApiRoutinesRoutineIdBaselinesGet,
  routineProposalCountsApiRoutinesProposalCountsGet,
  routineSweepsApiRoutinesRoutineIdSweepsGet,
  routineTrendApiRoutinesTrendGet,
  runDeltaApiRunsChunkIdGet,
  runRoutineApiRoutinesRoutineIdRunPost,
  supersedeFindingsApiFindingsSupersedePost,
  wontFixFindingsApiFindingsWontFixPost,
} from './lib/api/hub';
export type { ChunkDetail } from './lib/api/hub';
