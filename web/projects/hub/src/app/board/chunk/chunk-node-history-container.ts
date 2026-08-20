import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import {
  asyncState,
  deriveTranscriptSteps,
  filterArtifactsByStep,
  type hubApi,
  injectHubChunkTranscriptSegmentQuery,
  injectHubChunkTranscriptsQuery,
  type KitAsyncStateValue,
  parseNodeStepKey,
  resolveSegmentSeams,
  sortArtifacts,
  type TranscriptSegmentIndexEntry,
  type TranscriptStep,
  TranscriptFetchError,
} from 'fleet';

import { ChunkNodeHistoryTab } from './chunk-node-history-tab';

/**
 * The Node history tab's own container (`bzh:frontend-container-presentational`), the
 * same split as {@link ChunkTranscriptsContainer} — owns the transcript-index query the
 * per-step transcript panel needs (blizzard#248 D8: no new endpoint; the same read the
 * Transcripts tab already mounts), forwarding resolved state to the presentational
 * {@link ChunkNodeHistoryTab}, which injects nothing. `ChunkPage` mounts this only inside
 * its `@case ('node-history')` branch, keeping the query lazy the same way.
 *
 * A step can carry more than one segment (a resumed lease). {@link pickedSegmentId}
 * is this container's own local UI state — never URL-held, unlike the step selection
 * itself — and {@link effectiveSegmentId} falls back to the step's first segment
 * whenever the pick names nothing in the currently selected step: a step change makes
 * a stale pick from the *previous* step fall back automatically, since no two segments
 * ever share an id. {@link resolveSegmentSeams} (already shared with the Transcripts
 * tab, `transcripts/transcript-steps.ts`) resolves the continued-from/continues-in
 * links the presentational tab renders as seam buttons, the same way
 * `ChunkTranscriptsContainer` does for its own tab — this pane pages through every
 * segment of a step rather than reading only the first and pointing elsewhere for
 * the rest.
 */
@Component({
  selector: 'app-chunk-node-history-container',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkNodeHistoryTab],
  templateUrl: './chunk-node-history-container.html',
  styleUrl: './chunk-node-history-container.css',
})
export class ChunkNodeHistoryContainer {
  /** See {@link ChunkTranscriptsContainer.chunkId} — nullable for the same reason. */
  readonly chunkId = input.required<string | null>();
  readonly detail = input.required<hubApi.ChunkDetail>();
  readonly selectedKey = input<string | null>(null);
  readonly pickStep = output<string | null>();

  protected readonly indexQuery = injectHubChunkTranscriptsQuery(() => this.chunkId());

  private readonly parsedSelection = computed(() => {
    const key = this.selectedKey();
    return key === null ? null : parseNodeStepKey(key);
  });

  protected readonly stepArtifacts = computed(() => {
    const selection = this.parsedSelection();
    if (selection === null) return [];
    return sortArtifacts(filterArtifactsByStep(this.detail().artifacts ?? [], selection.nodeId, selection.epoch));
  });

  /** Every transcript step (D5's own groups), the same derivation the Transcripts tab
   * reads over the same index — {@link selectedStepSegments} and the seam resolution
   * below both read this rather than re-deriving it. */
  private readonly steps = computed<readonly TranscriptStep[]>(() => {
    const d = this.detail();
    return deriveTranscriptSteps(this.indexQuery.data()?.segments ?? [], d.history ?? [], {
      nodeId: d.current_node_id,
      nodeName: d.current_node_name ?? null,
      epoch: d.latest_epoch,
    });
  });

  /** The selected step's own segments, in `spawn_generation` order. */
  private readonly selectedStepSegments = computed<readonly TranscriptSegmentIndexEntry[]>(() => {
    if (this.parsedSelection() === null) return [];
    return this.steps().find((s) => s.key === this.selectedKey())?.segments ?? [];
  });

  /** The operator's own segment pick within the selected step — reset implicitly by a
   * step change, never explicitly (see {@link effectiveSegmentId}). */
  private readonly pickedSegmentId = signal<string | null>(null);

  /** The segment actually shown: {@link pickedSegmentId} when it still names one of
   * {@link selectedStepSegments}, else that step's first (its original recording). A
   * pick surviving a step change can never match the new step's own segment ids, so
   * this falls back on its own without an explicit reset. */
  protected readonly effectiveSegmentId = computed<string | null>(() => {
    const segments = this.selectedStepSegments();
    const picked = this.pickedSegmentId();
    if (picked !== null && segments.some((s) => s.segment_id === picked)) return picked;
    return segments[0]?.segment_id ?? null;
  });

  /** See {@link ChunkTranscriptsContainer.selectedSegmentFinal} — same trap, same fix. */
  private readonly effectiveSegmentFinal = computed<boolean | null>(() => {
    if (this.indexQuery.isPending()) return null;
    return this.selectedStepSegments().find((s) => s.segment_id === this.effectiveSegmentId())?.final ?? false;
  });

  protected readonly segmentQuery = injectHubChunkTranscriptSegmentQuery(
    () => this.chunkId(),
    () => this.effectiveSegmentId(),
    () => this.effectiveSegmentFinal(),
  );

  /** The effective segment's own resume-seam links — {@link resolveSegmentSeams} over
   * this container's {@link steps}, the same call `ChunkTranscriptsContainer` makes for
   * its own tab. */
  private readonly seams = computed(() => resolveSegmentSeams(this.steps(), this.effectiveSegmentId()));

  protected readonly continuedFrom = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuedFrom);

  protected readonly continuesIn = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuesIn);

  /** A seam button followed, or a segment picked directly — becomes the tab's own next
   * {@link effectiveSegmentId}. */
  protected onPickSegment(segmentId: string): void {
    this.pickedSegmentId.set(segmentId);
  }

  protected readonly isForbidden = computed(() => {
    const err = this.indexQuery.error();
    return err instanceof TranscriptFetchError && err.status === 403;
  });

  protected readonly indexState = computed<KitAsyncStateValue>(() => asyncState(this.indexQuery, false));

  /** `asyncState()`'s disabled-query trap (`query-state.ts`) — branched the same way
   * {@link ChunkTranscriptsContainer.segmentState} branches it: no step selected, or a
   * selected step with no segments at all, is this component's own rest state, resolved
   * before the query's own loading/error/ready fold. */
  protected readonly segmentState = computed<KitAsyncStateValue>(() => {
    if (this.selectedKey() === null || this.effectiveSegmentId() === null) return 'empty';
    return asyncState(this.segmentQuery, false);
  });
}
