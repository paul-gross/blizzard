/*
 * `fleet`'s test-helper entrypoint — a **second**, tsconfig
 * path-mapped barrel (`fleet/testing`, `web/tsconfig.json`), deliberately
 * separate from `fleet`'s production `public-api.ts` so a test helper never
 * reaches a production bundle.
 */

export { settle } from './settle';
export { hiddenAtContainerWidth, resolveContainerStyle } from './container-query';
export {
  stubRequestClient,
  stubError,
  type RequestClientStub,
  type CapturedRequest,
  type StubHttpError,
} from './stub-request-client';
export { OPERATOR_ME_RESPONSE } from './auth-fixtures';
export { FIXTURE_NOW } from './builders/clock';
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
export { chunkCountsView, fleetSpendView } from './builders/board';
