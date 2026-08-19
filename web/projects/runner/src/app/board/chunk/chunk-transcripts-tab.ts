import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, type KitChipOption, KitChips, KitPanel } from 'fleet';
import { TranscriptPanel } from 'local-panel';

/**
 * The runner chunk detail page's Transcripts tab (issue #318 follow-up) — the
 * attempts async state, the per-attempt {@link KitChips} picker, and the
 * open attempt's {@link TranscriptPanel}, extracted verbatim out of the page
 * so {@link ChunkDetailPage} is left with the queries and derived signals
 * rather than this region's own layout. Given a tab to itself rather than
 * sharing the page's stacked column with five other sections, it now fills
 * the available height instead of the page's old hard-coded `height: 480px`.
 *
 * Presentational only: the attempt state and options in, the picked lease id
 * back out — the page still owns `?attempt=` (D4) and the queries feeding
 * both inputs.
 */
@Component({
  selector: 'app-chunk-transcripts-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitChips, KitPanel, TranscriptPanel],
  templateUrl: './chunk-transcripts-tab.html',
  styleUrl: './chunk-transcripts-tab.css',
})
export class ChunkTranscriptsTab {
  /** `asyncState()` over the leases query, gated on this chunk's own filtered
   * attempts being empty. */
  readonly attemptsState = input.required<KitAsyncStateValue>();

  /** One selectable chip per attempt, keyed by lease id. */
  readonly attemptOptions = input<readonly KitChipOption[]>([]);

  /** The attempt whose transcript renders. */
  readonly activeAttemptLeaseId = input<string | null>(null);

  /** Emitted with a lease id when the operator picks a different attempt. */
  readonly selectAttempt = output<string>();
}
