import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { TransitionView } from '../api/hub';
import { harnessName } from '../core/harness-name';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitButton } from '../kit/kit-button';
import { KitMasterDetail } from '../kit/kit-master-detail';
import { KitSelectRow } from '../kit/kit-select-row';
import { encodeSidechainPath, parseSidechainPath, resolveSidechainByPath } from './transcript-sidechain-path';
import { deriveTranscriptSteps, resolveSegmentSeams, type TranscriptStep } from './transcript-steps';
import type { TranscriptSegmentContentView, TranscriptSegmentIndexEntry } from '../api/hub';
import { mergeLateLinks } from './merge-late-links';
import { TranscriptSegmentView } from './transcript-segment-view';
import { type SidechainOpenEvent, TranscriptViewer } from './transcript-viewer';

/**
 * The chunk detail page's Transcripts tab — a nav of node-history steps, each holding
 * its segments, beside a lazily-fetched segment viewer, composed into {@link KitMasterDetail}'s
 * split the way `ChunkArtifactsPanel` is — the kit owns the split, its phone drill-down, and
 * its Back control; the loading/forbidden/error/no-steps states render outside it. Like the
 * panel it is presentational (`bzh:frontend-container-presentational`):
 * the two reads behind this tab (the index on open, one segment's turns only once
 * opened) arrive as resolved-state inputs — nothing about a chunk's transcripts is in `detail()`'s own payload
 * (pinned at `test_chunk_detail_carries_no_transcript_field`).
 *
 * {@link indexState}/{@link segmentState} are the two reads' resolved async states
 * (`bzh:frontend-empty-state-gated`); {@link isForbidden} is carried
 * separately since a 403 on the index read is its own honest state, not a generic error.
 *
 * The open segment's seam buttons, truncated/turn-cap banners, and turn list all render
 * through {@link TranscriptSegmentView}.
 */
@Component({
  selector: 'fleet-chunk-transcripts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitMasterDetail, KitSelectRow, TranscriptSegmentView, TranscriptViewer],
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
  /** `ChunkDetail.current_node_terminal` — the current node is the graph's reserved
   * terminal, so it names no in-flight step. */
  readonly currentNodeTerminal = input(false);

  /** The `injectChunkTranscriptsQuery` read, resolved: the segment index once {@link indexState} is `'ready'`, `[]` otherwise. */
  readonly segments = input<readonly TranscriptSegmentIndexEntry[]>([]);

  /** `asyncState()` over the index query — never `'empty'`; "no segments yet" is this component's own {@link steps}-derived state. */
  readonly indexState = input.required<KitAsyncStateValue>();

  /** Whether the index read came back 403, checked ahead of {@link indexState}'s generic `'error'` (its own honest state). */
  readonly isForbidden = input(false);

  /** The `?segment` URL param — the open segment, or `null`. */
  readonly segmentId = input<string | null>(null);

  /** The `?sidechain` URL param, raw — a dot-joined `SidechainPath` (`fleet`'s `transcript-sidechain-path.ts`), or `null`. */
  readonly sidechainPath = input<string | null>(null);

  /** Opt-in phone drill-down presentation. An unselected tab is a segment list;
   * a URL-selected segment (including a stale one) is its detail state only.
   * The default retains simultaneous list and detail. */
  readonly drilldown = input(false);

  /** The `injectChunkTranscriptSegmentQuery` read: `'empty'` while {@link segmentId} names nothing, else loading/error/ready. */
  readonly segmentState = input.required<KitAsyncStateValue>();

  /** The open segment's turns and completion state, once {@link segmentState} is `'ready'`. */
  readonly segmentData = input<TranscriptSegmentContentView | undefined>(undefined);

  /** Emitted with a segment id when the operator picks it, or `null` to close one. */
  readonly pickSegment = output<string | null>();

  /** Emitted with an encoded `SidechainPath` when the operator opens a sidechain standalone, or `null` to return. */
  readonly pickSidechain = output<string | null>();

  protected readonly hasSelection = computed(() => this.segmentId() !== null);

  protected readonly steps = computed<readonly TranscriptStep[]>(() =>
    deriveTranscriptSteps(this.segments(), this.history(), {
      nodeId: this.currentNodeId(),
      nodeName: this.currentNodeName(),
      epoch: this.latestEpoch(),
      terminal: this.currentNodeTerminal(),
    }),
  );

  /** The open segment's resume-seam links — the pure derivation itself
   * lives beside {@link deriveTranscriptSteps}, tested there without a
   * mounted fixture; this component only resolves it against its own {@link steps}. */
  private readonly seams = computed(() => resolveSegmentSeams(this.steps(), this.segmentId()));

  protected readonly continuedFrom = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuedFrom);

  protected readonly continuesIn = computed<TranscriptSegmentIndexEntry | null>(() => this.seams().continuesIn);

  /** The open segment's own index entry — carries the recorded
   * `harness_id` {@link TranscriptSegmentView} renders; `null` while
   * nothing is open or the id names none of {@link steps}' own segments. */
  protected readonly openSegmentEntry = computed<TranscriptSegmentIndexEntry | null>(() => {
    const id = this.segmentId();
    if (id === null) return null;
    for (const step of this.steps()) {
      const found = step.segments.find((s) => s.segment_id === id);
      if (found) return found;
    }
    return null;
  });

  protected readonly harnessName = harnessName;

  /** {@link segmentData}'s turns with every late link folded onto its call.
   * Derived ONCE, shared between {@link TranscriptSegmentView} (which caps and renders
   * it) and the standalone path resolver below: merging fewer turns than the path is
   * resolved against would open the wrong sidechain. */
  protected readonly mergedTurns = computed(() => mergeLateLinks(this.segmentData()?.turns ?? []));

  /** {@link sidechainPath}, parsed — `[]` when none is open. */
  private readonly parsedSidechainPath = computed(() => parseSidechainPath(this.sidechainPath()));

  /** The sidechain opened standalone, or `null` — walks
   * {@link parsedSidechainPath} down through every nesting level it names, not just a
   * single top-level index: a nested sidechain's own turns index independently from 0. */
  protected readonly standaloneSidechain = computed(() =>
    resolveSidechainByPath(this.mergedTurns(), this.parsedSidechainPath()),
  );

  /** A top-level "open standalone" click — the event's path is already the full address
   * from the segment's top-level turns. */
  protected onTopLevelOpenStandalone(event: SidechainOpenEvent): void {
    this.pickSidechain.emit(encodeSidechainPath(event.path));
  }

  /** An "open standalone" click from *within* the standalone view — its path is relative to
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
