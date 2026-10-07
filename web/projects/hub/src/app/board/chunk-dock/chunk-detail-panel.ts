import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { type ChunkDetail, type ChunkStatus, type AnswerQuestionEvent, ChunkAwaitingHuman, type ResolveDecisionEvent, ChunkDelivery, ChunkFacts, type EditGraphEvent, ChunkIssuePane, ChunkTimeline, ChunkTokenBreakdown, type WorkItemsState } from 'fleet';
import { ChunkArtifacts } from './chunk-artifacts';
import { ChunkDetailHeader } from './chunk-detail-header';

export type { AnswerQuestionEvent, ResolveDecisionEvent } from 'fleet';
// Re-exported alongside the panel's own outputs, so a consumer binding them
// imports their event types from here.
export type { EditGraphEvent } from 'fleet';

/**
 * The chunk detail dock — everything known about the
 * selected chunk, filling the centre column under the board without reflowing it.
 *
 * A thin **composition** of seven sibling presentational
 * components under `lib/chunk/chunk-detail/`, each owning one region: the
 * {@link ChunkDetailHeader} (identity, pause/detach/resume, close), the
 * work-item column ({@link ChunkFacts} + {@link ChunkTokenBreakdown} +
 * {@link ChunkAwaitingHuman} + {@link ChunkIssuePane}), the node-history
 * {@link ChunkTimeline}, and the artifacts column ({@link ChunkArtifacts}).
 * This panel forwards `detail`/`workItems`/`actionError` down to whichever
 * siblings need them and re-emits their outputs up unchanged.
 *
 * `ChunkTokenBreakdown` is content-projected into `ChunkFacts`'s
 * `[token-breakdown]` slot so the cost/token rows land between Attempts and
 * Graph in one continuous `<dl class="kv">`.
 *
 * Presentational only: every server call (the mutations these events drive,
 * and the error any of them surfaces back down as `actionError`) lives in
 * the container. All color comes from the design-token layer, never
 * hard-coded, and every text size from that layer's type scale.
 */
@Component({
  selector: 'app-chunk-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkDetailHeader,
    ChunkDelivery,
    ChunkFacts,
    ChunkTokenBreakdown,
    ChunkIssuePane,
    ChunkTimeline,
    ChunkAwaitingHuman,
    ChunkArtifacts,
  ],
  templateUrl: './chunk-detail-panel.html',
  styleUrl: './chunk-detail-panel.css',
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related work items + fetch state, rendered by the Issue tab.
   * Defaults to `loading` so the panel constructs without the container wiring it. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** The container's last **operator-action** failure for this chunk (the 409/404
   * surfaced, not swallowed), or `null` when there is nothing to report.
   * One notice for every action in this dock (detach, pause, resume, complete). */
  readonly actionError = input<string | null>(null);

  /** The container's last operator-action **outcome** for this chunk — a non-failure
   * result that still needs saying, today the winning answer a lost
   * first-write-wins race returns. Rendered as news, not as a failure. */
  readonly actionOutcome = input<string | null>(null);

  /** Whether the current identity may pause/resume/detach or set the chunk's graph
   * (`chunk:control`), forwarded to {@link ChunkDetailHeader} and
   * {@link ChunkFacts}. `null`/pending resolves to `false`. */
  readonly canControl = input(false);

  /** The graphs view's own path segments, forwarded to {@link ChunkFacts} and
   * {@link ChunkTimeline} — `null` (the default) withholds the Graph fact's and every
   * timeline row's link, since this panel is exported from `fleet` too (`ChunkDetail`'s own public API) and
   * must not hardcode the hub-only `/graphs` route a runner-mounted instance has
   * nowhere to send. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Whether the current identity may answer an open question (`question:answer`),
   * forwarded to {@link ChunkAwaitingHuman}. */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`),
   * forwarded to {@link ChunkAwaitingHuman}. */
  readonly canResolve = input(false);

  /** Whether the pause/resume mutation is in flight, forwarded to {@link ChunkDetailHeader}. */
  readonly pausePending = input(false);

  /** Whether the detach mutation is in flight, forwarded to {@link ChunkDetailHeader}. */
  readonly detachPending = input(false);

  /** Whether the complete mutation is in flight, forwarded to {@link ChunkDetailHeader}. */
  readonly completePending = input(false);

  /** Whether the delete mutation is in flight, forwarded to {@link ChunkDetailHeader}. */
  readonly deletePending = input(false);

  /** Whether the resolve-decision mutation is in flight, forwarded to
   * {@link ChunkAwaitingHuman}. */
  readonly resolvePending = input(false);

  /** Whether a graph repin is in flight for this chunk, forwarded to {@link ChunkFacts}. */
  readonly graphPending = input(false);

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

  /** Emitted with the chunk id when the operator confirms Detach. */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause. Named
   * `pauseChunk`, not `pause` — `@angular-eslint/no-output-native` forbids an output
   * shadowing the native DOM `pause` event. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume. */
  readonly resumeChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Complete. */
  readonly complete = output<string>();

  /** Emitted with the chunk id when the operator confirms Delete. */
  readonly delete = output<string>();

  /** Emitted when the operator sets a not-ready chunk's graph from the facts column. */
  readonly editGraph = output<EditGraphEvent>();

  /** The chunk's work ref count — legible before the forge read lands, for the
   * work-item column's own heading. */
  protected readonly pointerCount = computed<number>(() => this.detail().work_refs?.length ?? 0);

}
