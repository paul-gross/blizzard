import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail } from '../api/hub';
import { ChunkArtifacts } from './chunk-artifacts';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  type ResolveDecisionEvent,
} from './chunk-awaiting-human';
import { ChunkDetailHeader } from './chunk-detail-header';
import { ChunkFacts, type EditGraphEvent } from './chunk-facts';
import { ChunkIssuePane } from './chunk-issue-pane';
import { ChunkTimeline } from './chunk-timeline';
import { ChunkTokenBreakdown } from './chunk-token-breakdown';
import type { WorkItemsState } from './work-items-state';

export type { AnswerQuestionEvent, ResolveDecisionEvent } from './chunk-awaiting-human';
// The container (`chunk-detail.ts`) imports these three event types from here too —
// re-exported so it needs no edit for the split (issue #79's container-unchanged
// invariant). `WorkItemsState` is not among them: the container takes it straight
// from `work-items-state.ts`, this feature's own owner of that fold.
export type { EditGraphEvent } from './chunk-facts';

/**
 * The chunk detail dock (MVP criterion 9/11) — everything known about the
 * selected chunk, filling the centre column under the board without reflowing it.
 *
 * A thin **composition** (issue #79) of seven sibling presentational
 * components under `lib/chunk-detail/`, each owning one region: the
 * {@link ChunkDetailHeader} (identity, pause/detach/resume, close), the
 * work-item column ({@link ChunkFacts} + {@link ChunkTokenBreakdown} +
 * {@link ChunkAwaitingHuman} + {@link ChunkIssuePane}), the node-history
 * {@link ChunkTimeline}, and the artifacts column ({@link ChunkArtifacts}).
 * This panel forwards `detail`/`workItems`/`actionError` down to whichever
 * siblings need them and re-emits their outputs up unchanged, so
 * `chunk-detail.ts`'s (the container's) template binding set is identical to
 * before the split.
 *
 * `ChunkTokenBreakdown` is content-projected into `ChunkFacts`'s
 * `[token-breakdown]` slot so the cost/token rows land between Attempts and
 * Graph in one continuous `<dl class="kv">` — the exact row order the
 * monolith rendered.
 *
 * Presentational only: every server call (the mutations these events drive,
 * and the error any of them surfaces back down as `actionError`) lives in
 * the container. All color comes from the design-token layer, never
 * hard-coded, and every text size from that layer's type scale.
 */
@Component({
  selector: 'fleet-chunk-detail-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDetailHeader, ChunkFacts, ChunkTokenBreakdown, ChunkIssuePane, ChunkTimeline, ChunkAwaitingHuman, ChunkArtifacts],
  templateUrl: './chunk-detail-panel.html',
  styleUrl: './chunk-detail-panel.css',
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related work items + fetch state, rendered by the Issue tab (issue #24).
   * Defaults to `loading` so the panel constructs without the container wiring it. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** The container's last **operator-action** failure for this chunk (the 409/404
   * surfaced, not swallowed — issue #42), or `null` when there is nothing to report.
   * One notice for every action in this dock (detach, pause, resume, complete). */
  readonly actionError = input<string | null>(null);

  /** The container's last operator-action **outcome** for this chunk — a non-failure
   * result that still needs saying (issue #165), today the winning answer a lost
   * first-write-wins race returns. Rendered as news, not as a failure. */
  readonly actionOutcome = input<string | null>(null);

  /** Whether the current identity may pause/resume/detach or set the chunk's graph
   * (`chunk:control` — issue #210), forwarded to {@link ChunkDetailHeader} and
   * {@link ChunkFacts}. `null`/pending resolves to `false`. */
  readonly canControl = input(false);

  /** Whether the current identity may answer an open question (`question:answer` —
   * issue #210), forwarded to {@link ChunkAwaitingHuman}. */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve` —
   * issue #210), forwarded to {@link ChunkAwaitingHuman}. */
  readonly canResolve = input(false);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted with the chunk id when the operator confirms Detach (issue #42). */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause (issue #46). Named
   * `pauseChunk`, not `pause` — `@angular-eslint/no-output-native` forbids an output
   * shadowing the native DOM `pause` event. */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume (issue #46). */
  readonly resumeChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Complete (issue #294). */
  readonly complete = output<string>();

  /** Emitted when the operator sets a not-ready chunk's graph from the facts column
   * (issue #27). */
  readonly editGraph = output<EditGraphEvent>();

  /** The chunk's work ref count — legible before the forge read lands, for the
   * work-item column's own heading. */
  protected readonly pointerCount = computed<number>(() => this.detail().work_refs?.length ?? 0);
}
