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
  templateUrl: './chunk-general-tab.html',
  styleUrl: './chunk-general-tab.css',
})
export class ChunkGeneralTab {
  /** The chunk aggregate to render. */
  readonly detail = input.required<hubApi.ChunkDetail>();

  /** The chunk's related work-source items + fetch state. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** Whether the current identity may set the chunk's graph (`chunk:control` —
   * issue #210), forwarded to {@link ChunkFacts}. */
  readonly canControl = input(false);

  /** Whether the current identity may answer an open question (`question:answer` —
   * issue #210), forwarded to {@link ChunkAwaitingHuman}. */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve` —
   * issue #210), forwarded to {@link ChunkAwaitingHuman}. */
  readonly canResolve = input(false);

  /** Emitted when the operator answers an open question. */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted when the operator sets a not-ready chunk's graph. */
  readonly editGraph = output<EditGraphEvent>();

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);
}
