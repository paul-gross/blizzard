import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkTimeline,
  ChunkTokenBreakdown,
  type EditGraphEvent,
  type hubApi,
  KitPanel,
  type ResolveDecisionEvent,
  type WorkItemsState,
} from 'fleet';

/**
 * The chunk detail page's General tab (issue #160) — everything the page
 * showed before it grew a second tab, extracted verbatim so
 * {@link ChunkPage} is left with the tab strip and the queries/mutations that
 * feed every tab, not this region's own layout.
 *
 * Every section is the same `fleet` presentational sibling the desktop dock
 * composes (`bzh:frontend-kit`) — {@link ChunkFacts} + {@link ChunkTokenBreakdown},
 * {@link ChunkIssuePane}, {@link ChunkTimeline}, {@link ChunkAwaitingHuman} — this
 * component only picks the arrangement: a two-column grid at ≥720px
 * (blizzard#203) — work item and issues stacked in the left column, node
 * history beside them spanning both rows, asks · decisions spanning the full
 * width below — collapsing to one stacked column, DOM order, below it.
 * Presentational only: inputs in, the three operator-action outputs back
 * out, no injection.
 */
@Component({
  selector: 'app-chunk-general-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkAwaitingHuman, ChunkFacts, ChunkIssuePane, ChunkTimeline, ChunkTokenBreakdown, KitPanel],
  template: `
    <div class="general" data-testid="chunk-general-tab">
      <fleet-kit-panel class="section" data-testid="section-work-item" label="work item" [count]="pointerCount() || null">
        <fleet-chunk-detail-facts [detail]="detail()" (editGraph)="editGraph.emit($event)">
          <fleet-chunk-detail-token-breakdown token-breakdown [detail]="detail()" />
        </fleet-chunk-detail-facts>
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-issues" label="issues">
        <fleet-chunk-detail-issue-pane [workItems]="workItems()" />
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-node-history" label="node history">
        <fleet-chunk-detail-timeline [detail]="detail()" />
      </fleet-kit-panel>
      <fleet-kit-panel class="section" data-testid="section-asks" label="asks · decisions">
        <fleet-chunk-detail-awaiting-human
          [detail]="detail()"
          (answerQuestion)="answerQuestion.emit($event)"
          (resolveDecision)="resolveDecision.emit($event)"
        />
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
    /* The mockup's two-column General tab, at the same 720px breakpoint the
       Artifacts tab's nav-beside-viewer split uses — work item and issues
       stacked in the left column, node history beside them spanning both
       rows, asks · decisions spanning the full width below. */
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

  /** Emitted when the operator answers an open question. */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted when the operator sets a not-ready chunk's graph. */
  readonly editGraph = output<EditGraphEvent>();

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);
}
