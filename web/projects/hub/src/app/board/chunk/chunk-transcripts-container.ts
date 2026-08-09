import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  asyncState,
  injectHubChunkTranscriptSegmentQuery,
  injectHubChunkTranscriptsQuery,
  type KitAsyncStateValue,
  TranscriptFetchError,
  type TransitionView,
} from 'fleet';

import { ChunkTranscriptsTab } from './chunk-transcripts-tab';

/**
 * The Transcripts tab's own container (`bzh:frontend-container-presentational`,
 * `review:F1`) — owns its two queries (D8: the index on open, one segment's turns only
 * once opened) and maps their loading/error state, forwarding resolved data down to the
 * presentational {@link ChunkTranscriptsTab}, which carries the tab's markup and injects
 * nothing. Split out of {@link ChunkPage} rather than folded into it, so that container
 * stays under `web:structural-gate`'s line cap; {@link ChunkPage} mounts this only inside
 * its `@case ('transcripts')` branch, which is what keeps these two queries lazy — they
 * fire only once that tab is actually selected, the same as before this split.
 */
@Component({
  selector: 'app-chunk-transcripts-container',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkTranscriptsTab],
  template: `
    <app-chunk-transcripts-tab
      [history]="history()"
      [currentNodeId]="currentNodeId()"
      [currentNodeName]="currentNodeName()"
      [latestEpoch]="latestEpoch()"
      [segments]="indexQuery.data()?.segments ?? []"
      [indexState]="indexState()"
      [isForbidden]="isForbidden()"
      [segmentId]="segmentId()"
      [sidechainTurnIndex]="sidechainTurnIndex()"
      [segmentState]="segmentState()"
      [segmentData]="segmentQuery.data()"
      (pickSegment)="pickSegment.emit($event)"
      (pickSidechain)="pickSidechain.emit($event)"
    />
  `,
})
export class ChunkTranscriptsContainer {
  readonly chunkId = input.required<string>();
  readonly history = input.required<readonly TransitionView[]>();
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);
  readonly segmentId = input<string | null>(null);
  readonly sidechainTurnIndex = input<string | null>(null);

  readonly pickSegment = output<string | null>();
  readonly pickSidechain = output<string | null>();

  protected readonly indexQuery = injectHubChunkTranscriptsQuery(() => this.chunkId());
  protected readonly segmentQuery = injectHubChunkTranscriptSegmentQuery(() => this.chunkId(), () => this.segmentId());

  protected readonly isForbidden = computed(() => {
    const err = this.indexQuery.error();
    return err instanceof TranscriptFetchError && err.status === 403;
  });

  protected readonly indexState = computed<KitAsyncStateValue>(() => asyncState(this.indexQuery, false));

  /** `asyncState()`'s own documented trap (`query-state.ts`): a disabled query reports
   * `isPending()` forever, so the "no segment selected" rest state is branched here,
   * before falling into the query's own loading/error/ready fold. */
  protected readonly segmentState = computed<KitAsyncStateValue>(() => {
    if (this.segmentId() === null) return 'empty';
    return asyncState(this.segmentQuery, false);
  });
}
