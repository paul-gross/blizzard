export { ChunkDetailPanel } from './chunk-detail-panel';
export type { AnswerQuestionEvent, ResolveDecisionEvent } from './chunk-detail-panel';
export { ChunkDetail } from './chunk-detail';
// The dock's presentational siblings, exported individually so a second shell can
// re-stack the same regions without forking any of them (`bzh:frontend-kit`) — the
// hub's mobile chunk page composes exactly these, in one column instead of three.
export { ChunkArtifactBody } from './chunk-artifact-body';
export { ChunkArtifacts } from './chunk-artifacts';
export { sortArtifacts } from './sort-artifacts';
export { ChunkAwaitingHuman } from './chunk-awaiting-human';
export { ChunkFacts } from './chunk-facts';
export type { EditGraphEvent } from './chunk-facts';
export { ChunkIssuePane } from './chunk-issue-pane';
export type { WorkItemsState } from './chunk-issue-pane';
export { ChunkTimeline } from './chunk-timeline';
export { ChunkTokenBreakdown } from './chunk-token-breakdown';
export type { TransitionView, ArtifactView, DecisionView, EscalationView, ChunkUsageTotalView, ChunkUsageView } from '../api/hub';
