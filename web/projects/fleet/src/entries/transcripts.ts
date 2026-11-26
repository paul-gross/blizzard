/* The `fleet/transcripts` entry point: the transcript viewer, segments, and reads. */

export { mergeLateLinks } from '../lib/transcripts/merge-late-links';
export { TranscriptSegmentView } from '../lib/transcripts/transcript-segment-view';
export {
  injectChunkTranscriptSegmentQuery,
  injectChunkTranscriptsQuery,
  TranscriptFetchError,
} from '../lib/transcripts/transcript-segments.query';
export { deriveTranscriptSteps, resolveSegmentSeams, type TranscriptStep } from '../lib/transcripts/transcript-steps';
export { translateTranscriptTurn } from '../lib/transcripts/transcript-turn.translate';
export type { TranscriptSegmentContent, TranscriptTurn } from '../lib/transcripts/transcript-turn';
export { TranscriptViewer } from '../lib/transcripts/transcript-viewer';
