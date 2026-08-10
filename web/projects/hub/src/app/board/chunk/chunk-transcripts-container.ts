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
 *
 * `:host { display: contents }` (`review:F1`, round-2 regression fix) — this component
 * contributes no box of its own, so its single child (`app-chunk-transcripts-tab`)
 * becomes a direct flex item of {@link ChunkPage}'s `.cp-body` the way it was before this
 * container existed. Without it, the tab's own `:host { flex: 1; min-height: 0 }`
 * (`chunk-transcripts-tab.ts`) has no flex ancestor to apply against — this container's
 * own box, laid out in normal block flow — and resolves to `height: auto`, which breaks
 * the tab's internal `height: 100%` chain all the way down to `.tx-view`'s scroll
 * container, so a long segment becomes unreachable, clipped by `ChunkPage`'s
 * `.cp { overflow: hidden }` with nothing to scroll.
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
      [sidechainPath]="sidechainPath()"
      [segmentState]="segmentState()"
      [segmentData]="segmentQuery.data()"
      (pickSegment)="pickSegment.emit($event)"
      (pickSidechain)="pickSidechain.emit($event)"
    />
  `,
  styles: `
    :host {
      display: contents;
    }
  `,
})
export class ChunkTranscriptsContainer {
  readonly chunkId = input.required<string>();
  readonly history = input.required<readonly TransitionView[]>();
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);
  readonly segmentId = input<string | null>(null);
  readonly sidechainPath = input<string | null>(null);

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
