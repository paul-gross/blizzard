import { ChangeDetectionStrategy, Component, computed, inject, input, output, signal } from '@angular/core';

import type { ChunkDetail, TranscriptSegmentIndexEntry } from '../../api/hub';
import type { KitAsyncStateValue } from '../../kit/kit-async-state';
import { asyncState, restingAsyncState } from '../../core/query-state';
import {
  injectChunkTranscriptSegmentQuery,
  injectChunkTranscriptsQuery,
  TranscriptFetchError,
} from '../../transcripts/transcript-segments.query';
import { effectiveSegmentId, segmentFinal, stepSegments } from '../../transcripts/transcript-selection.model';
import { deriveTranscriptSteps, resolveSegmentSeams, type TranscriptStep } from '../../transcripts/transcript-steps';
import { CHUNK_PAGE_DAEMON } from './chunk-page-daemon';
import { parseSelectedKey, stepArtifacts } from './chunk-node-history.model';
import { ChunkNodeHistoryTab } from './chunk-node-history-tab';

/**
 * The Node history tab's own container (`bzh:frontend-container-presentational`) — owns
 * the transcript-index and segment queries the per-step transcript panel needs,
 * forwarding resolved state to the presentational {@link ChunkNodeHistoryTab}, which
 * injects nothing.
 *
 * The transcript reads cross the daemon's own seam ({@link CHUNK_PAGE_DAEMON}'s `client`
 * and `plane`), so each daemon's page shows the per-step transcript from its own
 * transcript endpoints.
 *
 * A step can carry more than one segment (a resumed lease). {@link pickedSegmentId}
 * is this container's own local UI state — never URL-held, unlike the step selection
 * itself — and {@link effectiveSegmentId} falls back to the step's first segment
 * whenever the pick names nothing in the currently selected step: a step change makes
 * a stale pick from the *previous* step fall back automatically, since no two segments
 * ever share an id. {@link resolveSegmentSeams} resolves the continued-from/continues-in
 * links the presentational tab renders as seam buttons, so this pane pages through every
 * segment of a step.
 */
@Component({
  selector: 'fleet-chunk-node-history-container',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkNodeHistoryTab],
  templateUrl: './chunk-node-history-container.html',
  styleUrl: './chunk-node-history-container.css',
})
export class ChunkNodeHistoryContainer {
  /** See {@link ChunkTranscriptsContainer.chunkId} — nullable for the same reason. */
  readonly chunkId = input.required<string | null>();
  readonly detail = input.required<ChunkDetail>();
  readonly selectedKey = input<string | null>(null);
  /** {@link ChunkTimelineSelection.graphLinkBase}, forwarded — `null` leaves graph
   * badges unlinked. */
  readonly graphLinkBase = input<readonly string[] | null>(null);
  /** Opts the presentational tab into the hub phone's list/detail presentation. */
  readonly drilldown = input(false);
  readonly pickStep = output<string | null>();

  private readonly daemon = inject(CHUNK_PAGE_DAEMON);

  protected readonly indexQuery = injectChunkTranscriptsQuery(
    () => this.daemon.client,
    () => this.daemon.plane,
    () => this.chunkId(),
  );

  private readonly parsedSelection = computed(() => parseSelectedKey(this.selectedKey()));

  protected readonly stepArtifacts = computed(() =>
    stepArtifacts(this.detail().artifacts ?? [], this.parsedSelection()),
  );

  /** Every transcript step derived from the index — {@link selectedStepSegments} and the seam resolution
   * below both read this rather than re-deriving it. */
  private readonly steps = computed<readonly TranscriptStep[]>(() => {
    const d = this.detail();
    return deriveTranscriptSteps(this.indexQuery.data()?.segments ?? [], d.history ?? [], {
      nodeId: d.current_node_id,
      nodeName: d.current_node_name ?? null,
      epoch: d.latest_epoch,
      terminal: d.current_node_terminal ?? false,
    });
  });

  /** The selected step's own segments, in `spawn_generation` order. */
  private readonly selectedStepSegments = computed<readonly TranscriptSegmentIndexEntry[]>(() =>
    stepSegments(this.steps(), this.parsedSelection(), this.selectedKey()),
  );

  /** The operator's own segment pick within the selected step — reset implicitly by a
   * step change, never explicitly (see {@link effectiveSegmentId}). */
  private readonly pickedSegmentId = signal<string | null>(null);

  /** The segment actually shown: {@link pickedSegmentId} when it still names one of
   * {@link selectedStepSegments}, else that step's first (its original recording). A
   * pick surviving a step change can never match the new step's own segment ids, so
   * this falls back on its own without an explicit reset. */
  protected readonly effectiveSegmentId = computed<string | null>(() =>
    effectiveSegmentId(this.selectedStepSegments(), this.pickedSegmentId()),
  );

  /** See {@link ChunkTranscriptsContainer.selectedSegmentFinal} — same trap, same fix. */
  private readonly effectiveSegmentFinal = computed<boolean | null>(() =>
    segmentFinal(this.indexQuery.isPending(), this.selectedStepSegments(), this.effectiveSegmentId()),
  );

  protected readonly segmentQuery = injectChunkTranscriptSegmentQuery(
    () => this.daemon.client,
    () => this.daemon.plane,
    () => this.chunkId(),
    () => this.effectiveSegmentId(),
    () => this.effectiveSegmentFinal(),
  );

  /** The effective segment's own resume-seam links — {@link resolveSegmentSeams} over
   * this container's {@link steps}. */
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

  /** `asyncState()`'s disabled-query trap (`query-state.ts`): no step selected, or a
   * selected step with no segments at all, is this component's own rest state, resolved
   * before the query's own loading/error/ready fold. */
  protected readonly segmentState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.selectedKey() === null || this.effectiveSegmentId() === null, this.segmentQuery, false),
  );
}
