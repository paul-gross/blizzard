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
  template: `
    <div class="general">
      <fleet-kit-panel class="section" data-testid="section-work-item" label="work item" [count]="pointerCount() || null">
        <fleet-chunk-detail-facts [detail]="detail()">
          <fleet-chunk-detail-token-breakdown token-breakdown [detail]="detail()" />
        </fleet-chunk-detail-facts>
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-issues" label="issues">
        <fleet-chunk-detail-issue-pane [workItems]="workItems()" placement="inline" />
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-node-history" label="node history">
        <!-- Heading suppressed: the enclosing panel's label already says it. See
             the hub's own general tab for the same opt-out and why the default
             is true (the board dock relies on the internal heading). -->
        <fleet-chunk-detail-timeline [detail]="detail()" [heading]="false" />
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-asks" label="asks · decisions">
        <fleet-chunk-detail-awaiting-human [detail]="detail()" />
      </fleet-kit-panel>
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
    }
    .general {
      display: flex;
      flex-direction: column;
      gap: 8px;
      padding: 8px;
    }
    fleet-kit-panel.section {
      flex: none;
    }
    /* The hub's own two-column General tab, at the same 720px breakpoint —
       work item and issues stacked in the left column, node history beside
       them spanning both rows, asks · decisions spanning the full width
       below. */
    @media (min-width: 720px) {
      .general {
        display: grid;
        grid-template-columns: 1fr 1fr;
        align-content: start;
      }
      fleet-kit-panel[data-testid='section-work-item'] {
        grid-column: 1;
        grid-row: 1;
      }
      fleet-kit-panel[data-testid='section-issues'] {
        grid-column: 1;
        grid-row: 2;
      }
      fleet-kit-panel[data-testid='section-node-history'] {
        grid-column: 2;
        grid-row: 1 / 3;
      }
      fleet-kit-panel[data-testid='section-asks'] {
        grid-column: 1 / -1;
        grid-row: 3;
      }
    }
  `,
})
export class ChunkGeneralTab {
  /** The chunk aggregate to render. */
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The chunk's related work-source items + fetch state. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);
}
