import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { TransitionView } from '../api/hub';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { encodeSidechainPath, parseSidechainPath, resolveSidechainByPath } from './transcript-sidechain-path';
import { deriveTranscriptSteps, resolveSegmentSeams, type TranscriptStep } from './transcript-steps';
import type { TranscriptSegmentContentView, TranscriptSegmentIndexEntry } from '../api/hub';
import { mergeLateLinks } from './merge-late-links';
import { TranscriptSegmentView } from './transcript-segment-view';
import { type SidechainOpenEvent, TranscriptViewer } from './transcript-viewer';

/**
 * The chunk detail page's Transcripts tab (blizzard#248 Phase 2, moved into `fleet` by
 * runner-node-grouped-transcripts Phase 4 so both the hub and runner apps mount the same
 * component) — a nav of node-history steps, each holding its segments, beside a
 * lazily-fetched segment viewer. Mirrors `ChunkArtifactsTab`'s nav-beside-viewer shape
 * and, like it, is presentational (`bzh:frontend-container-presentational`, `review:F1`):
 * the two queries behind this tab (D8: the index on open, one segment's turns only once
 * opened) live on {@link ChunkTranscriptsContainer}, which passes their resolved state
 * down as inputs — nothing about a chunk's transcripts is in `detail()`'s own payload
 * (D8, pinned at `test_chunk_detail_carries_no_transcript_field`).
 *
 * {@link indexState}/{@link segmentState} are that container's own `asyncState()` folds over
 * its two queries (`bzh:frontend-empty-state-gated`); {@link isForbidden} is carried
 * separately since a 403 on the index read is its own honest state (D9), not a generic error.
 *
 * The open segment's seam buttons, truncated/turn-cap banners, and turn list all render
 * through {@link TranscriptSegmentView} — the same shared body the node history tab's
 * per-step detail pane mounts, so neither carries its own copy of that markup.
 */
@Component({
  selector: 'fleet-chunk-transcripts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, TranscriptSegmentView, TranscriptViewer],
  templateUrl: './chunk-transcripts-tab.html',
  styleUrl: './chunk-transcripts-tab.css',
})
export class ChunkTranscriptsTab {
  /** `ChunkDetail.history` — the node-history steps to group segments under. */
  readonly history = input.required<readonly TransitionView[]>();

  /** `ChunkDetail.current_node_id`/`.current_node_name`/`.latest_epoch` — the in-flight step. */
  readonly currentNodeId = input<string | null>(null);
  readonly currentNodeName = input<string | null>(null);
  readonly latestEpoch = input<number | null>(null);

  /** The `injectChunkTranscriptsQuery` read, resolved: the segment index once {@link indexState} is `'ready'`, `[]` otherwise. */
  readonly segments = input<readonly TranscriptSegmentIndexEntry[]>([]);

  /** `asyncState()` over the index query — never `'empty'`; "no segments yet" is this component's own {@link steps}-derived state. */
  readonly indexState = input.required<KitAsyncStateValue>();

  /** Whether the index read came back 403 (D9), checked ahead of {@link indexState}'s generic `'error'` (its own honest state). */
  readonly isForbidden = input(false);

  /** The `?segment` URL param — the open segment, or `null`. */
  readonly segmentId = input<string | null>(null);

  /** The `?sidechain` URL param, raw (`review:F3`) — a dot-joined `SidechainPath` (`fleet`'s `transcript-sidechain-path.ts`), or `null`. */
  readonly sidechainPath = input<string | null>(null);

  /** The `injectChunkTranscriptSegmentQuery` read: `'empty'` while {@link segmentId} names nothing, else loading/error/ready. */
  readonly segmentState = input.required<KitAsyncStateValue>();

  /** The open segment's turns and completion state, once {@link segmentState} is `'ready'`. */
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** Emitted with a segment id when the operator picks it, or `null` to close one. */
  readonly pickSegment = output<string | null>();

  /** Emitted with an encoded `SidechainPath` (`review:F3`) when the operator opens a sidechain standalone, or `null` to return. */
  readonly pickSidechain = output<string | null>();

  protected readonly steps = computed<readonly TranscriptStep[]>(() =>
    deriveTranscriptSteps(this.segments(), this.history(), {
      nodeId: this.currentNodeId(),
      nodeName: this.currentNodeName(),
      epoch: this.latestEpoch(),
    }),
  );

  /** The open segment's resume-seam links (blizzard#248 D6) — the pure derivation itself
   * lives beside {@link deriveTranscriptSteps} (`review:F11`), tested there without a
   * mounted fixture; this component only resolves it against its own {@link steps}. */
  private readonly seams = computed(() => resolveSegmentSeams(this.steps(), this.segmentId()));

  protected readonly continuedFrom = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuedFrom);

  protected readonly continuesIn = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuesIn);

  /** {@link segmentData}'s turns with every late link folded onto its call (blizzard#338).
   * Derived ONCE, shared between {@link TranscriptSegmentView} (which caps and renders
   * it) and the standalone path resolver below: merging fewer turns than the path is
   * resolved against would open the wrong sidechain. */
  protected readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));

  /** {@link sidechainPath}, parsed — `[]` when none is open. */
  private readonly parsedSidechainPath = computed(() => parseSidechainPath(this.sidechainPath()));

  /** The sidechain opened standalone (D7, `review:F3`), or `null` — walks
   * {@link parsedSidechainPath} down through every nesting level it names, not just a
   * single top-level index: a nested sidechain's own turns index independently from 0. */
  protected readonly standaloneSidechain = computed(() =>
    resolveSidechainByPath(this.mergedTurns(), this.parsedSidechainPath()),
  );

  /** A top-level "open standalone" click — the event's path is already the full address
   * from the segment's top-level turns (`review:F3`). */
  protected onTopLevelOpenStandalone(event: SidechainOpenEvent): void {
    this.pickSidechain.emit(encodeSidechainPath(event.path));
  }

  /** An "open standalone" click from *within* the standalone view (`review:F3`; this
   * binding didn't exist before, so these controls were dead) — its path is relative to
   * the already-open sidechain's own turns, so the full address prepends
   * {@link parsedSidechainPath} in front. */
  protected onStandaloneOpenStandalone(event: SidechainOpenEvent): void {
    this.pickSidechain.emit(encodeSidechainPath([...this.parsedSidechainPath(), ...event.path]));
  }

  protected stepLabel(step: TranscriptStep): string {
    const name = step.nodeName ?? step.nodeId ?? '—';
    return step.epoch === null ? name : `${name} · epoch ${step.epoch}`;
  }
}
