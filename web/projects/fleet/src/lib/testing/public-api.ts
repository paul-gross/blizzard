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
} from './transport/stub-request-client';
export { OPERATOR_ME_RESPONSE, flagsResponse, meResponse, meTenant, providerSummary, runnerAuthSessionView } from './auth-fixtures.translate';
export { chunkControlStub, type ChunkControlStub, type ChunkControlVerb } from './chunk-control-stub';
export { FIXTURE_NOW, fixtureInstant } from './builders/clock';
export { awaitingHuman, awaitingQuestion, answeredQuestion, awaitingDecision, recordingAwaitingHumanActions } from './builders/awaiting-human';
export type { AnswerCall, ResolveCall } from './builders/awaiting-human';
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
} from './builders/chunk.translate';
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
} from './builders/artifact.translate';
export {
  turnSegmentView,
  toolCallSegmentView,
  sidechainSegmentView,
  transcriptSegmentIndexEntry,
  transcriptSegmentContentView,
} from './builders/transcript.translate';
export { chunkCountsView, chunkSummary, fleetSpendView } from './builders/board.translate';
export { graphView, graphNodeView, graphEdgeView } from './builders/graph.translate';
export { pauseStateView, capacitiesView, hubConnectivityView, runnerStatusView, runnerDashboardView, hostRunnerView } from './builders/runner-dashboard.translate';
export { hostEnvironmentView, hostRegistrationView, servedProjectStatusView, selfTestView, hostSubscriptionView, harnessHealthView } from './builders/host-shell-views.translate';
export { runnerRegistryView, hostRegistryView, runnerBindingView, subscriptionUsageView } from './builders/fleet-runner-host.translate';
export { projectView, sourceLinkView, repositoryLinkView, workSourceSummary, repositorySummary, secretView, memberView, forgeDetail, credentialSummary, credentialDocument, configChangeView, configChangesPage } from './builders/admin-config.translate';
export { queuePeekEntry, backlogPeekEntry } from './builders/board-root-queue.translate';
export { runnerLeaseView, runnerAskView, runnerFactView } from './builders/runner-core.translate';
export { gardenFindingDetailView, gardenFindingFactView, gardenProposalCountsRowView, gardenProposalCountsView, gardenTrendPeriodView, gardenTrendView, gardenScopeSweepView, gardenMeasurementReadingView, gardenSweepsView, gardenGraphSummaryView } from './builders/garden.translate';
export { gardenProposalView, gardenProposalClosureView, gardenProposalAcceptResponse, gardenProposalsPageView, gardenEvidenceFindingView } from './builders/garden-proposal.translate';
export { deliveredSetView, deliveredSetDeltaView, runEscalationView, runRowView, runDeltaView, routineRunResponse, routineBaselineView, gardenRoutineView, gardenScopeView, gardenProjectView } from './builders/garden-run.translate';
export { gardenCadenceView, gardenChoreView, gardenCadenceAttentionView, gardenCadenceErrandView, gardenErrandRunView, runCadenceView } from './builders/cadence.translate';
export { gardenWorkItemView } from './builders/garden.translate';
export { gardenFindingView } from './builders/garden.translate';
export { graphSummaryView } from './builders/graph-summary.translate';
export { eventView } from './builders/event-log.translate';
export { flowView, flowFactView } from './builders/delivery-flow.translate';

export { assetChunkArtifact, commitChunkArtifact, surveyChunkArtifact, deltaChunkArtifact, bounceChunkArtifact, domainFindingCandidate, domainFindingSurvey, domainFindingDelta, domainAddFindingOp, domainObservedFindingOp, domainGoneFindingOp } from './builders/artifact';
export { chunkArtifactStore, chunkEscalation, domainChunkDetail, domainDecision, domainQuestion, domainTransition, domainPr, domainLandedRepo, domainRoute, domainBounce, domainUsage, domainUsageTotal, domainQueuedChunk, domainMidFlightChunk, domainBouncedChunk, domainEscalatedChunk, domainLandedChunk, domainStoppedChunk, workItem } from './builders/chunk';
