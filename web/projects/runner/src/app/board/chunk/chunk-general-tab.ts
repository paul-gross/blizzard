import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import {
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkTimeline,
  ChunkTokenBreakdown,
  type hubApi,
  KitPanel,
  type WorkItemsState,
} from 'fleet';

/**
 * The runner chunk detail page's General tab (issue #318 follow-up) —
 * everything the page showed before it grew a tab strip, extracted verbatim
 * so {@link ChunkDetailPage} is left with the route params, the queries, and
 * the tab strip rather than this region's own layout, the same split the
 * hub's own `ChunkGeneralTab` makes on `chunk-page.ts`.
 *
 * The same two-column grid at ≥720px the hub's tab uses (blizzard#203) — work
 * item and issues stacked in the left column, node history beside them
 * spanning both rows, asks · decisions spanning the full width below —
 * collapsing to one stacked column, DOM order, below it. Presentational
 * only: a `detail`/`workItems` input in, no operator-action outputs — this
 * route mounts the shared `fleet` sections read-only, same as before.
 */
@Component({
  selector: 'app-chunk-general-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkAwaitingHuman, ChunkFacts, ChunkIssuePane, ChunkTimeline, ChunkTokenBreakdown, KitPanel],
  templateUrl: './chunk-general-tab.html',
  styleUrl: './chunk-general-tab.css',
})
export class ChunkGeneralTab {
  /** The chunk aggregate to render. */
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The chunk's related work-source items + fetch state. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);
}
