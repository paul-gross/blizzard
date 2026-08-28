import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { ChunkIssueList } from '../chunk-issue-list';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { type WorkItemsState } from './work-items-state';

/**
 * The work item's issue pass-through (issue #24, issue #79) — the chunk's
 * linked forge issue(s): title, body, and messages. Owns its own
 * loading/error/empty triad through the shared kit's async-state component
 * rather than a re-typed `<p class="status">`, and delegates the
 * resolved items to {@link ChunkIssueList} — this pane's own concern stays the
 * fetch triad, the list's is the per-issue accordion row. Presentational
 * only; the forge read itself lives in the container.
 *
 * `placement` (issue #318) forwards to the inner `fleet-kit-async-state`,
 * defaulting to its own `'center'` — every existing mount (the desktop dock's
 * `chunk-detail-panel.ts`, the shared `chunk-page/chunk-general-tab.ts` both
 * apps compose) keeps its prior rendering unchanged. The runner's narrow
 * single-column chunk detail route is the one caller that opts into
 * `'inline'`: its full-sentence error copy overflowed `'center'`'s
 * absolutely-positioned box at phone widths, an issue a wide desktop layout
 * never hits.
 */
@Component({
  selector: 'fleet-chunk-detail-issue-pane',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkIssueList, KitAsyncState],
  templateUrl: './chunk-issue-pane.html',
  styleUrl: './chunk-issue-pane.css',
})
export class ChunkIssuePane {
  /** The chunk's related work items + fetch state, from the container (issue #24).
   * Defaults to `loading` so the pane constructs without the container wiring it. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** Forwarded to the inner `fleet-kit-async-state` — `'center'` (the default,
   * every existing mount's prior behavior) or `'inline'` (the runner's narrow
   * chunk detail route, issue #318). */
  readonly placement = input<'center' | 'inline'>('center');

  /** The async triad's resolved state — loading/error take precedence, then no
   * linked issue, else the issue items render. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    const state = this.workItems();
    if (state.status === 'loading') return 'loading';
    if (state.status === 'error') return 'error';
    return state.items.length === 0 ? 'empty' : 'ready';
  });
}
