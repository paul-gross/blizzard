import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { Client } from '../api/hub/client';
import type { TransitionView } from '../api/hub';
import { asyncState } from '../query-state';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import type { TranscriptPlane } from '../query-keys';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';
import { injectChunkTranscriptSegmentQuery, injectChunkTranscriptsQuery, TranscriptFetchError } from './transcript-segments.query';

/**
 * The Transcripts tab's own container (`bzh:frontend-container-presentational`,
 * `review:F1`) — owns its two queries (D8: the index on open, one segment's turns only
 * once opened) and maps their loading/error state, forwarding resolved data down to the
 * presentational {@link ChunkTranscriptsTab}, which carries the tab's markup and injects
 * nothing. Moved into `fleet` (runner-node-grouped-transcripts Phase 4) so both the hub
 * and runner apps mount the identical component; {@link client}/{@link plane} are the seam
 * each app crosses to reach its own copy of the identically-shaped route (D5) — required
 * inputs, never defaulted, so a mounting app states which plane it reads from rather than
 * this component guessing or branching on it.
 *
 * `:host { display: contents }` (`review:F1`, round-2 regression fix) — this component
 * contributes no box of its own, so its single child (`fleet-chunk-transcripts-tab`)
 * becomes a direct flex item of the mounting page's own body the way it was before this
 * container existed. Without it, the tab's own `:host { flex: 1; min-height: 0 }`
 * (`chunk-transcripts-tab.ts`) has no flex ancestor to apply against — this container's
 * own box, laid out in normal block flow — and resolves to `height: auto`, which breaks
 * the tab's internal `height: 100%` chain all the way down to `.tx-view`'s scroll
 * container, so a long segment becomes unreachable, clipped by the page's own
 * `overflow: hidden` with nothing to scroll.
 */
@Component({
  selector: 'fleet-chunk-transcripts-container',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkTranscriptsTab],
  templateUrl: './chunk-transcripts-container.html',
  styleUrl: './chunk-transcripts-container.css',
})
export class ChunkTranscriptsContainer {
  /** Which daemon's API this instance reads from (D5) — the hub app passes `hubClient`,
   * the runner app its own `runnerClient`; both generated clients share this structural
   * shape (`bzh:generated-client`). */
  readonly client = input.required<Client>();

  /** Namespaces this instance's TanStack cache keys (D5) — see {@link client}'s own doc. */
  readonly plane = input.required<TranscriptPlane>();

  /** `ChunkPage.chunkId()`'s own type — nullable everywhere else that field is threaded
   * (`review:F12`) — so the query's own `enabled: id !== null` stays the real gate
   * rather than a `?? ''` sentinel that could pass it with an empty id. */
  readonly chunkId = input.required<string | null>();
  readonly history = input.required<readonly TransitionView[]>();
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);
  readonly segmentId = input<string | null>(null);
  readonly sidechainPath = input<string | null>(null);

  readonly pickSegment = output<string | null>();
  readonly pickSidechain = output<string | null>();

  protected readonly indexQuery = injectChunkTranscriptsQuery(
    () => this.client(),
    () => this.plane(),
    () => this.chunkId(),
  );

  /** The selected segment's own `final`, resolved from the already-fetched index
   * (`review:F2`) — `null` until the index names it, so the read below is never issued
   * against a placement that is only a guess. A segment the resolved index does not list
   * falls to `false`, the still-live placement, which is the safe way to be wrong. */
  protected readonly selectedSegmentFinal = computed<boolean | null>(() => {
    if (this.indexQuery.isPending()) return null;
    return this.indexQuery.data()?.segments?.find((s) => s.segment_id === this.segmentId())?.final ?? false;
  });

  /** One query, whose key placement (`review:F2`'s two keys) is chosen once finality is
   * actually known — `selectedSegmentFinal()`'s `null` holds it disabled until then, so the
   * segment is read once rather than once per placement. One instance, not one per
   * placement: two gated instances read the segment exactly as often, and only spread the
   * one decision this component makes across two fields and a selector between them. */
  protected readonly segmentQuery = injectChunkTranscriptSegmentQuery(
    () => this.client(),
    () => this.plane(),
    () => this.chunkId(),
    () => this.segmentId(),
    () => this.selectedSegmentFinal(),
  );

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
