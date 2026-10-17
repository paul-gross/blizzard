import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { TransitionView } from '../api/hub';
import { asyncState, restingAsyncState } from '../core/query-state';
import type { KitAsyncStateValue } from '../kit/kit-async-state';
import type { DaemonTarget } from '../core/daemon-target/daemon-target';
import { ChunkTranscriptsTab } from './chunk-transcripts-tab';
import { injectChunkTranscriptSegmentQuery, injectChunkTranscriptsQuery, TranscriptFetchError } from './transcript-segments.query';
import { segmentFinal } from './transcript-selection.model';

/**
 * The Transcripts tab's own container (`bzh:frontend-container-presentational`)
 * — owns its two queries (the index on open, one segment's turns only
 * once opened) and maps their loading/error state, forwarding resolved data down to the
 * presentational {@link ChunkTranscriptsTab}, which carries the tab's markup and injects
 * nothing. {@link target} is the seam a mounting app crosses to reach its
 * own copy of the identically-shaped route — a required
 * input, never defaulted, so a mounting app states which daemon it reads from rather than
 * this component guessing or branching on it.
 *
 * `:host { display: contents }` — this component
 * contributes no box of its own, so its single child is a direct flex item of
 * whatever mounts it.
 */
@Component({
  selector: 'fleet-chunk-transcripts-container',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkTranscriptsTab],
  templateUrl: './chunk-transcripts-container.html',
  styleUrl: './chunk-transcripts-container.css',
})
export class ChunkTranscriptsContainer {
  /** The daemon this instance reads transcripts from — its generated client
   * (`bzh:generated-client`) and the key root its TanStack cache keys lead with; `null`
   * holds both reads disabled. */
  readonly target = input.required<DaemonTarget | null>();

  /** The chunk whose transcripts to read, or `null` while none is known — nullable
   * so the query's own `enabled: id !== null` stays the real gate
   * rather than a `?? ''` sentinel that could pass it with an empty id. */
  readonly chunkId = input.required<string | null>();
  readonly history = input.required<readonly TransitionView[]>();
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);
  readonly currentNodeTerminal = input(false);
  readonly segmentId = input<string | null>(null);
  readonly sidechainPath = input<string | null>(null);
  /** Opts a caller into the phone's list-or-detail presentation; defaults to
   * the established simultaneous list/detail presentation. */
  readonly drilldown = input(false);

  readonly pickSegment = output<string | null>();
  readonly pickSidechain = output<string | null>();

  protected readonly indexQuery = injectChunkTranscriptsQuery(
    () => this.target(),
    () => this.chunkId(),
  );

  /** The selected segment's own `final` ({@link segmentFinal}). */
  protected readonly selectedSegmentFinal = computed<boolean | null>(() =>
    segmentFinal(this.indexQuery.isPending(), this.indexQuery.data()?.segments ?? [], this.segmentId()),
  );

  /** One query, whose key placement (two keys) is chosen once finality is
   * actually known — `selectedSegmentFinal()`'s `null` holds it disabled until then, so the
   * segment is read exactly once. */
  protected readonly segmentQuery = injectChunkTranscriptSegmentQuery(
    () => this.target(),
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
  protected readonly segmentState = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.segmentId() === null, this.segmentQuery, false),
  );
}
