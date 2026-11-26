/* The `fleet/chunk-detail` entry point: the chunk detail's light sections. */

export type { ParsedBounceEnvelope } from '../lib/chunk/chunk-detail/artifact-structured.model';
export type { ChunkArtifact, ChunkArtifactStore } from '../lib/chunk/chunk-detail/artifact.model';
export { pauseCopy, resumeCopy } from '../lib/chunk/chunk-detail/chunk-action-copy';
export { ChunkArtifactBody } from '../lib/chunk/chunk-detail/chunk-artifact-body';
export type { ChunkControl } from '../lib/chunk/chunk-detail/chunk-control';
export { ChunkDelivery } from '../lib/chunk/chunk-detail/chunk-delivery';
export { ChunkEscalation } from '../lib/chunk/chunk-detail/chunk-escalation';
export { ChunkFacts } from '../lib/chunk/chunk-detail/chunk-facts';
export { ChunkTimeline } from '../lib/chunk/chunk-detail/chunk-timeline';
export { ChunkUrgency } from '../lib/chunk/chunk-detail/chunk-urgency';
export { compactBounce } from '../lib/chunk/chunk-detail/compact-bounce';
export { EscalationTakeover } from '../lib/chunk/chunk-detail/escalation-takeover';
export { filterArtifactsByStep } from '../lib/chunk/chunk-detail/filter-artifacts-by-step';
export { chunkGraphLabel } from '../lib/chunk/chunk-detail/graph-label';
export { hasDelivery } from '../lib/chunk/chunk-detail/has-delivery';
export { bounceReason, parseBounceEnvelope } from '../lib/chunk/chunk-detail/parse-bounce-envelope';
export { sortArtifacts } from '../lib/chunk/chunk-detail/sort-artifacts';
export type { WorkItem } from '../lib/chunk/chunk-detail/work-item.model';
export {
  deriveWorkItemsState,
  type WorkItemsQuery,
  type WorkItemsState,
} from '../lib/chunk/chunk-detail/work-items-state';
