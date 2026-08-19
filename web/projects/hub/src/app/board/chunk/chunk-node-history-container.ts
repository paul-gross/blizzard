import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  asyncState,
  deriveTranscriptSteps,
  filterArtifactsByStep,
  type hubApi,
  injectHubChunkTranscriptSegmentQuery,
  injectHubChunkTranscriptsQuery,
  type KitAsyncStateValue,
  parseNodeStepKey,
  sortArtifacts,
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
 * Only the selected step's own segments are ever fetched — every other step's segments
 * stay unread until picked. A step's segments are read in `spawn_generation` order; this
 * container fetches only the **first** (the step's original recording), since fetching
 * every resumed segment concurrently has no query-hook precedent in this codebase yet.
 * A step with more than one segment says so ({@link extraSegmentCount}) rather than
 * silently dropping the rest — the presentational tab links out to the Transcripts tab's
 * full seam navigation for that case.
 *
 * `:host { display: contents }` — see {@link ChunkTranscriptsContainer}'s own doc comment
 * for why: without it this component's own box breaks the presentational child's
 * `flex: 1; min-height: 0` chain down to its scroll container.
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

  /** The selected step's own segments, in `spawn_generation` order — {@link deriveTranscriptSteps}
   * is the same grouping the Transcripts tab reads, run here over the same index. */
  private readonly selectedStepSegments = computed(() => {
    const selection = this.parsedSelection();
    if (selection === null) return [];
    const d = this.detail();
    const steps = deriveTranscriptSteps(this.indexQuery.data()?.segments ?? [], d.history ?? [], {
      nodeId: d.current_node_id,
      nodeName: d.current_node_name ?? null,
      epoch: d.latest_epoch,
    });
    return steps.find((s) => s.key === this.selectedKey())?.segments ?? [];
  });

  private readonly primarySegmentId = computed(() => this.selectedStepSegments()[0]?.segment_id ?? null);

  protected readonly extraSegmentCount = computed(() => Math.max(0, this.selectedStepSegments().length - 1));

  /** See {@link ChunkTranscriptsContainer.selectedSegmentFinal} — same trap, same fix. */
  private readonly primarySegmentFinal = computed<boolean | null>(() => {
    if (this.indexQuery.isPending()) return null;
    return this.selectedStepSegments()[0]?.final ?? false;
  });

  protected readonly segmentQuery = injectHubChunkTranscriptSegmentQuery(
    () => this.chunkId(),
    () => this.primarySegmentId(),
    () => this.primarySegmentFinal(),
  );

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
    if (this.selectedKey() === null || this.primarySegmentId() === null) return 'empty';
    return asyncState(this.segmentQuery, false);
  });
}
