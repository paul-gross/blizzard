import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { type ChunkControl, type ChunkDetail, type ChunkStatus, type AnswerQuestionEvent, ChunkAwaitingHuman, type ResolveDecisionEvent, ChunkDelivery, ChunkFacts, ChunkIssuePane, ChunkTimeline, hasDelivery, type WorkItemsState, KitErrorLine } from 'fleet';
import { ChunkArtifacts } from './chunk-artifacts';
import { ChunkDetailHeader } from '../../core/chunk-detail-header/chunk-detail-header';

export type { AnswerQuestionEvent, ResolveDecisionEvent } from 'fleet';

/**
 * The chunk detail dock — a thin **composition** of presentational regions: the
 * {@link ChunkDetailHeader}, the work-item column ({@link ChunkFacts} +
 * {@link ChunkAwaitingHuman} + {@link ChunkIssuePane}),
 * the node-history {@link ChunkTimeline}, and the artifacts column
 * ({@link ChunkArtifacts}). It forwards its inputs down to the regions that need them
 * and re-emits their outputs unchanged.
 *
 * Presentational only (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-chunk-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkDetailHeader,
    ChunkDelivery,
    ChunkFacts,
    ChunkIssuePane,
    ChunkTimeline,
    ChunkAwaitingHuman,
    ChunkArtifacts,
    KitErrorLine,
  ],
  templateUrl: './chunk-detail-panel.html',
  styleUrl: './chunk-detail-panel.css',
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's project's color, forwarded to {@link ChunkDetailHeader}; `null` when unknown. */
  readonly projectColor = input<string | null>(null);

  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

  /** The chunk's related work items + fetch state, rendered by the Issue tab.
   * Defaults to `loading`. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** The last operator-action failure for this chunk, or `null` when there is nothing
   * to report. */
  readonly actionError = input<string | null>(null);

  /** The last operator-action **outcome** for this chunk — a non-failure result that
   * still needs saying. Rendered as news, not as a failure. */
  readonly actionOutcome = input<string | null>(null);

  /** The chunk-control port behind the header's ⋮ menu, forwarded to
   * {@link ChunkDetailHeader}; `null` hides the menu. */
  readonly control = input<ChunkControl | null>(null);

  /** The graphs view's own path segments, forwarded to {@link ChunkTimeline}'s heading; `null` withholds the graph label's link. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Whether the current identity may answer an open question (`question:answer`),
   * forwarded to {@link ChunkAwaitingHuman}. */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`),
   * forwarded to {@link ChunkAwaitingHuman}. */
  readonly canResolve = input(false);

  /** Whether the resolve-decision mutation is in flight, forwarded to
   * {@link ChunkAwaitingHuman}. */
  readonly resolvePending = input(false);

  /** The ids of the questions an answer mutation is in flight for, forwarded to
   * {@link ChunkAwaitingHuman}. */
  readonly pendingAnswerQuestionIds = input<readonly string[]>([]);

  /** The chunk's status as the status chip renders it, forwarded unchanged to
   * {@link ChunkDetailHeader.renderedStatus}. */
  readonly renderedStatus = input.required<ChunkStatus>();

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question. */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Whether the delivery section shows — `hasDelivery`. */
  protected readonly showDelivery = computed(() => hasDelivery(this.detail()));

  protected readonly pointerCount = computed<number>(() => this.detail().work_refs?.length ?? 0);

}
