import type { TranscriptSegmentIndexEntry } from '../api/hub';
import type { TranscriptStep } from './transcript-steps';

/**
 * The selected segment's own `final`, resolved from the already-fetched transcript
 * index — `null` while the index is still pending, so a segment read keyed on it is
 * never issued against a placement that is only a guess. A segment the resolved index
 * does not list falls to `false`, the still-live placement, which is the safe way to be
 * wrong.
 */
export function segmentFinal(
  indexPending: boolean,
  segments: readonly Pick<TranscriptSegmentIndexEntry, 'segment_id' | 'final'>[],
  segmentId: string | null,
): boolean | null {
  if (indexPending) return null;
  return segments.find((s) => s.segment_id === segmentId)?.final ?? false;
}

/**
 * The segment actually shown within a step: `pickedId` when it still names one of
 * `segments`, else the step's first (its original recording), else `null`. A pick
 * surviving a step change can never match the new step's own segment ids, so this
 * falls back on its own without an explicit reset.
 */
export function effectiveSegmentId(
  segments: readonly Pick<TranscriptSegmentIndexEntry, 'segment_id'>[],
  pickedId: string | null,
): string | null {
  if (pickedId !== null && segments.some((s) => s.segment_id === pickedId)) return pickedId;
  return segments[0]?.segment_id ?? null;
}

/**
 * The selected step's own segments, in the order the step carries them — none when the
 * selection key did not parse as a node-step (`selection` is `null`) or names no step.
 */
export function stepSegments<S>(
  steps: readonly (Pick<TranscriptStep, 'key'> & { readonly segments: readonly S[] })[],
  selection: { readonly nodeId: string; readonly epoch: number } | null,
  selectedKey: string | null,
): readonly S[] {
  if (selection === null) return [];
  return steps.find((s) => s.key === selectedKey)?.segments ?? [];
}
