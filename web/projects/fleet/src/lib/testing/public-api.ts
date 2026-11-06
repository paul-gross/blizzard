/*
 * `fleet`'s test-helper entrypoint — a **second**, tsconfig
 * path-mapped barrel (`fleet/testing`, `web/tsconfig.json`), deliberately
 * separate from `fleet`'s production `public-api.ts` so a test helper never
 * reaches a production bundle.
 */

export { settle } from './settle';
export { hiddenAtContainerWidth, resolveContainerStyle } from './container-query';
export {
  stubHubClient,
  stubRunnerClient,
  stubError,
  stubAnswerRaceLost,
  type StubUndeclaredError,
  type StubRoutes,
  type StubRouteHandler,
  type StubDetailBody,
  type RequestClientStub,
  type CapturedRequest,
  type StubHttpError,
} from './stub-request-client';
export { OPERATOR_ME_RESPONSE } from './auth-fixtures';
export { chunkControlStub, type ChunkControlStub, type ChunkControlVerb } from './chunk-control-stub';
export { FIXTURE_NOW, fixtureInstant } from './builders/clock';
export {
  chunkDetail,
  workRefView,
  transitionView,
  chunkEscalationView,
  chunkNeighborView,
  routeView,
  prView,
  landedRepoView,
  chunkUsageTotalView,
  questionView,
  decisionView,
  workItemEntry,
  bounceView,
  chunkUsageView,
  chunkNeighborhoodView,
  queuedChunkDetail,
  midFlightChunkDetail,
  bouncedChunkDetail,
  escalatedChunkDetail,
  landedChunkDetail,
  stoppedChunkDetail,
} from './builders/chunk';
export {
  assetArtifactView,
  commitArtifactView,
  findingSurvey,
  findingCandidate,
  findingDelta,
  addFindingOp,
  observedFindingOp,
  goneFindingOp,
  surveyArtifactView,
  deltaArtifactView,
  bounceArtifactView,
} from './builders/artifact';
export {
  turnSegmentView,
  toolCallSegmentView,
  sidechainSegmentView,
  transcriptSegmentIndexEntry,
  transcriptSegmentContentView,
} from './builders/transcript';
export { chunkCountsView, chunkSummary, fleetSpendView } from './builders/board';
export { graphView, graphNodeView, graphEdgeView } from './builders/graph';
export { pauseStateView, capacitiesView, hubConnectivityView, runnerStatusView, runnerDashboardView, hostRunnerView } from './builders/runner-dashboard';
export { hostEnvironmentView, hostRegistrationView, servedProjectStatusView, selfTestView, hostSubscriptionView, harnessHealthView } from './builders/host-shell-views';
export { runnerRegistryView, hostRegistryView, runnerBindingView, subscriptionUsageView } from './builders/fleet-runner-host';
export { projectView, sourceLinkView, repositoryLinkView, workSourceSummary, repositorySummary, secretView, memberView, forgeDetail, configChangeView, configChangesPage } from './builders/admin-config';
export { queuePeekEntry, backlogPeekEntry } from './builders/board-root-queue';
export { runnerLeaseView, runnerAskView, runnerFactView } from './builders/runner-core';
export { gardenFindingDetailView, gardenFindingFactView, gardenProposalCountsRowView, gardenProposalCountsView, gardenTrendPeriodView, gardenTrendView, gardenScopeSweepView, gardenMeasurementReadingView, gardenSweepsView, gardenGraphSummaryView } from './builders/garden';
export { gardenProposalView, gardenProposalClosureView, gardenProposalAcceptResponse, gardenProposalsPageView, gardenEvidenceFindingView } from './builders/garden-proposal';
export { deliveredSetView, deliveredSetDeltaView, runEscalationView, runRowView, runDeltaView, routineRunResponse, routineBaselineView, gardenRoutineView, gardenScopeView, gardenProjectView } from './builders/garden-run';
export { gardenCadenceView, gardenChoreView, gardenCadenceAttentionView, gardenCadenceErrandView, gardenErrandRunView, runCadenceView } from './builders/cadence';
export { gardenWorkItemView } from './builders/garden';
export { gardenFindingView } from './builders/garden';
export { graphSummaryView } from './builders/graph-summary';
export { eventView } from './builders/event-log';
export { flowView, flowFactView } from './builders/delivery-flow';
