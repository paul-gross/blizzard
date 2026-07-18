import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail } from '../api/hub';
import { ChunkArtifacts } from './chunk-artifacts';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  type ResolveDecisionEvent,
} from './chunk-awaiting-human';
import { ChunkDetailHeader } from './chunk-detail-header';
import { ChunkFacts, type EditGraphEvent, type EditModelEvent } from './chunk-facts';
import { ChunkIssuePane, type PmItemsState } from './chunk-issue-pane';
import { ChunkTimeline } from './chunk-timeline';
import { ChunkTokenBreakdown } from './chunk-token-breakdown';

export type { AnswerQuestionEvent, ResolveDecisionEvent } from './chunk-awaiting-human';
// The container (`chunk-detail.ts`) imports these three from here too — re-exported
// so it needs no edit for the split (issue #79's container-unchanged invariant).
export type { EditGraphEvent, EditModelEvent } from './chunk-facts';
export type { PmItemsState } from './chunk-issue-pane';

/**
 * The chunk detail dock (MVP criterion 9/11) — everything known about the
 * selected chunk, filling the centre column under the board without reflowing it.
 *
 * A thin **composition** (issue #79) of seven sibling presentational
 * components under `lib/chunk-detail/`, each owning one region: the
 * {@link ChunkDetailHeader} (identity, pause/detach/resume, close), the
 * work-item column ({@link ChunkFacts} + {@link ChunkTokenBreakdown} +
 * {@link ChunkIssuePane}), the node-history {@link ChunkTimeline}, and the
 * artifacts-and-asks column ({@link ChunkAwaitingHuman} + {@link ChunkArtifacts}).
 * This panel forwards `detail`/`pmItems`/`actionError` down to whichever
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
  template: `
    <aside class="dock" data-testid="chunk-detail" role="region" aria-label="Chunk detail">
      <fleet-chunk-detail-header
        [detail]="detail()"
        (dismiss)="dismiss.emit()"
        (detach)="detach.emit($event)"
        (pauseChunk)="pauseChunk.emit($event)"
        (resumeChunk)="resumeChunk.emit($event)"
      />

      @if (actionError(); as err) {
        <p class="notice action-notice" data-testid="action-error" role="alert">{{ err }}</p>
      }

      <div class="d-body">
        <section class="d-sec" aria-label="Work item">
          <div class="s-head"><span class="tag">Work item · {{ pointerCount() }}</span></div>
          <fleet-chunk-detail-facts
            [detail]="detail()"
            (editGraph)="editGraph.emit($event)"
            (editModel)="editModel.emit($event)"
          >
            <fleet-chunk-detail-token-breakdown token-breakdown [detail]="detail()" />
          </fleet-chunk-detail-facts>
          <fleet-chunk-detail-issue-pane [pmItems]="pmItems()" />
        </section>
        <section class="d-sec" aria-label="Node history">
          <fleet-chunk-detail-timeline [detail]="detail()" />
        </section>
        <section class="d-sec" aria-label="Artifacts and asks">
          <fleet-chunk-detail-awaiting-human
            [detail]="detail()"
            (answerQuestion)="answerQuestion.emit($event)"
            (resolveDecision)="resolveDecision.emit($event)"
          />
          <fleet-chunk-detail-artifacts [detail]="detail()" />
        </section>
      </div>
    </aside>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      min-height: 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    /* The dock itself never scrolls — each of its three columns owns its own
       scroll, so a long issue body cannot push the history and artifacts out of
       view, and the header stays pinned. */
    .dock {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      color: var(--text);
      overflow: hidden;
    }
    /* An operator action's failure reads between the header and the columns — the
       action's own result, next to the control that fired it, not buried in a column.
       One notice serves detach, pause, and resume alike. */
    .notice {
      margin: 0;
      padding: 4px 6px;
      border: 1px solid var(--red-dim);
      border-left-width: 2px;
      background: var(--overlay-20);
      color: var(--red);
      font-size: var(--fs-xs);
    }
    .action-notice {
      margin: 6px;
      flex: none;
    }
    /* Three columns of roughly equal weight: the work item, the path the chunk took,
       and what that produced (plus anything it is waiting on a human for). */
    .d-body {
      flex: 1;
      min-height: 0;
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 1px;
      background: var(--line);
      overflow: hidden;
    }
    .d-sec {
      background: var(--panel-deep);
      padding: 6px 8px;
      overflow-y: auto;
      min-height: 0;
      min-width: 0;
    }
    .s-head {
      margin-bottom: 6px;
    }
  `,
})
export class ChunkDetailPanel {
  /** The chunk aggregate to render (status, current node, history, artifacts). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related PM items + fetch state, rendered by the Issue tab (issue #24).
   * Defaults to `loading` so the panel constructs without the container wiring it. */
  readonly pmItems = input<PmItemsState>({ status: 'loading', items: [] });

  /** The container's last **operator-action** failure for this chunk (the 409/404
   * surfaced, not swallowed — issue #42), or `null` when there is nothing to report.
   * One notice for every action in this dock (detach, pause, resume). */
  readonly actionError = input<string | null>(null);

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

  /** Emitted when the operator sets a not-ready chunk's graph from the facts column
   * (issue #27). */
  readonly editGraph = output<EditGraphEvent>();

  /** Emitted when the operator sets a not-ready chunk's model from the facts column
   * (issue #27). */
  readonly editModel = output<EditModelEvent>();

  /** The chunk's PM pointer count — legible before the forge read lands, for the
   * work-item column's own heading. */
  protected readonly pointerCount = computed<number>(() => this.detail().pm_pointers?.length ?? 0);
}
