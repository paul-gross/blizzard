import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail } from '../api/hub';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkNeighborhood,
  ChunkTimeline,
  ChunkTokenBreakdown,
  type EditGraphEvent,
  type ResolveDecisionEvent,
  type WorkItemsState,
} from '../chunk-detail';
import { KitPanel } from '../kit/kit-panel';

/**
 * The chunk detail page's General tab (blizzard#337) — the hub's and the
 * runner's own `chunk-general-tab.ts` unified into the one `fleet` owner both
 * pages compose, alongside their other cross-app `chunk-page/` pieces
 * ({@link ChunkPageShell}, {@link ChunkPageHeader}).
 *
 * Every section is the same `fleet` presentational sibling the desktop dock
 * composes (`bzh:frontend-kit`) — {@link ChunkFacts} + {@link ChunkTokenBreakdown},
 * {@link ChunkIssuePane}, {@link ChunkNeighborhood}, {@link ChunkTimeline},
 * {@link ChunkAwaitingHuman} — this component only picks the arrangement: a
 * two-column grid at ≥720px (blizzard#203) — work item and issues stacked in
 * the left column, node history beside them spanning both rows, dependencies
 * and asks · decisions each spanning the full width below (issue #462 D7) —
 * collapsing to one stacked column, DOM order, below it.
 *
 * Presentational only, `bzh:frontend-container-presentational`: inputs in,
 * the three operator-action outputs plus {@link pickStep} back out, no
 * injection. The two call sites' variance is every input but `detail` and
 * `workItems`: {@link canControl}, {@link canAnswer}, {@link canResolve}, and
 * {@link graphLinkBase} default to exactly what the runner's own tab rendered
 * (off, unlinked), so the runner opts into nothing and the hub opts into all
 * four plus the three outputs; {@link issuePanePlacement} defaults to
 * {@link ChunkIssuePane}'s own `'center'` (the hub's rendering), and the
 * runner's narrow single-column route is the one caller that opts into
 * `'inline'`.
 */
@Component({
  selector: 'fleet-chunk-general-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkAwaitingHuman, ChunkFacts, ChunkIssuePane, ChunkNeighborhood, ChunkTimeline, ChunkTokenBreakdown, KitPanel],
  templateUrl: './chunk-general-tab.html',
  styleUrl: './chunk-general-tab.css',
})
export class ChunkGeneralTab {
  /** The chunk aggregate to render. */
  readonly detail = input.required<ChunkDetail>();

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

  /** The graphs view's own path segments, forwarded to {@link ChunkFacts} and
   * {@link ChunkTimeline} — `null` (the default, the runner's rendering) withholds
   * the link; the hub passes `['/graphs']`, the only app with that route. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Forwarded to {@link ChunkIssuePane}'s own `placement` — `'center'` (the
   * default, the hub's rendering) or `'inline'`, the runner's narrow chunk
   * detail route (issue #318). */
  readonly issuePanePlacement = input<'center' | 'inline'>('center');

  /** Emitted when the operator answers an open question. */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted when the operator sets a not-ready chunk's graph. */
  readonly editGraph = output<EditGraphEvent>();

  /** Emitted with a node's join key when the operator activates it in this tab's own
   * node-history summary — {@link ChunkTimeline.pickStep} forwarded straight through, a
   * pure activation signal this tab holds no selection state of its own for (the
   * container routes it to the Node history tab, where the row it names is selected). */
  readonly pickStep = output<string | null>();

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);
}
