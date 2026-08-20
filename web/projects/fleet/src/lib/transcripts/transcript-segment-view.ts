import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { TranscriptSegmentIndexEntry } from '../api/hub';
import type { SidechainOpenEvent } from './transcript-viewer';
import { TranscriptViewer } from './transcript-viewer';
import type { TranscriptTurn } from './transcript-turn';

/** Keep only the most recent this-many turns rendered for one segment — mirrors the
 * runner panel's own `MAX_TURNS` cap (`projected_transcript_repository.py`), so no
 * consumer renders an unbounded DOM for one large segment (`review:F7`). A sidechain's
 * own turns are uncapped, same as the runner side. */
const MAX_RENDERED_TURNS = 1000;

/**
 * One open transcript segment, rendered with its own resume-seam links (D6)
 * and truncation/cap banners — the shared body both the Transcripts tab and the
 * node history tab's per-step detail pane mount, factored out once a second consumer
 * needed the same seam buttons and turn cap the Transcripts tab already carried.
 * Presentational (`bzh:frontend-container-presentational`): a consumer resolves
 * {@link continuedFrom}/{@link continuesIn} against its own step (`resolveSegmentSeams`,
 * `transcript-steps.ts`) and owns which segment id is actually open.
 *
 * {@link turns} is expected already merged (`mergeLateLinks`) — a consumer that also
 * needs the merged list for something else (the Transcripts tab's own standalone
 * sidechain resolution) merges once and passes the same array here, rather than this
 * component merging a second time over content the caller already folded.
 */
@Component({
  selector: 'fleet-transcript-segment-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [TranscriptViewer],
  templateUrl: './transcript-segment-view.html',
  styleUrl: './transcript-segment-view.css',
})
export class TranscriptSegmentView {
  /** The open segment's turns, already merged (`mergeLateLinks`) — this component only
   * caps and renders them. */
  readonly turns = input.required<readonly TranscriptTurn[]>();

  /** Whether the open segment's own stored content was truncated at write time. */
  readonly truncated = input(false);

  /** The segment immediately before the open one in its own step (`resolveSegmentSeams`),
   * or `null` when the open segment is the step's first. */
  readonly continuedFrom = input<TranscriptSegmentIndexEntry | null>(null);

  /** The segment immediately after the open one in its own step, or `null` when the
   * open segment is the step's last (or the step's only) segment. */
  readonly continuesIn = input<TranscriptSegmentIndexEntry | null>(null);

  /** Emitted with a seam's target segment id when the operator follows it. */
  readonly pickSegment = output<string>();

  /** {@link TranscriptViewer.openStandalone}, forwarded unchanged — a consumer with no
   * standalone concept (the node history pane) needs no listener at all, since
   * {@link TranscriptViewer} always renders a sidechain inline too. */
  readonly openStandalone = output<SidechainOpenEvent>();

  /** {@link turns}, tail-capped at {@link MAX_RENDERED_TURNS} the same way the runner
   * panel caps its own list (`review:F7`). A sidechain's own turns pass through
   * {@link TranscriptViewer} uncapped, same as the runner side. */
  protected readonly cappedTurns = computed(() => {
    const turns = this.turns();
    return turns.length > MAX_RENDERED_TURNS ? turns.slice(-MAX_RENDERED_TURNS) : turns;
  });

  protected readonly turnsCapped = computed(() => this.turns().length > MAX_RENDERED_TURNS);

  protected readonly MAX_RENDERED_TURNS = MAX_RENDERED_TURNS;
}
