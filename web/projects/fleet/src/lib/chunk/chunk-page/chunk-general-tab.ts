import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { ChunkDetail } from '../../api/hub';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkTimeline,
  ChunkTokenBreakdown,
  type EditGraphEvent,
  type ResolveDecisionEvent,
  type WorkItemsState,
} from '../chunk-detail';
import { isPendingFor } from '../../core/mutation-pending';
import { KitPanel, KitPanelHeader } from '../../kit/kit-panel';
import { ChunkDelivery } from '../chunk-detail/chunk-delivery';

/**
 * The chunk detail page's General tab.
 *
 * Every section is a `fleet` presentational component (`bzh:frontend-kit`) —
 * {@link ChunkFacts} + {@link ChunkTokenBreakdown},
 * {@link ChunkIssuePane}, {@link ChunkTimeline},
 * {@link ChunkAwaitingHuman} — this component only picks the arrangement: a
 * two-column grid at ≥720px — work item (its facts and, when the
 * chunk has any, its dependency edges either way) and issues stacked in the left
 * column, node history beside them spanning both rows, asks · decisions spanning
 * the full width below — collapsing to one stacked column, DOM order, below it.
 *
 * Presentational only, `bzh:frontend-container-presentational`: inputs in,
 * the three operator-action outputs plus {@link pickStep} back out, no
 * injection. {@link canControl}, {@link canAnswer}, {@link canResolve}, and
 * {@link graphLinkBase} default to read-only (off, unlinked), so a caller with no
 * actions opts into nothing; {@link issuePanePlacement} defaults to
 * {@link ChunkIssuePane}'s own `'center'`.
 */
@Component({
  selector: 'fleet-chunk-general-tab',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkAwaitingHuman, ChunkFacts, ChunkIssuePane, ChunkTimeline, ChunkTokenBreakdown, ChunkDelivery, KitPanel, KitPanelHeader, NgTemplateOutlet, RouterLink],
  templateUrl: './chunk-general-tab.html',
  styleUrl: './chunk-general-tab.css',
})
export class ChunkGeneralTab {
  /** The chunk aggregate to render. */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's related work-source items + fetch state. */
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });

  /** Whether the current identity may set the chunk's graph (`chunk:control`),
   * forwarded to {@link ChunkFacts}. */
  readonly canControl = input(false);

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

  /** The ids of the chunks a graph repin is in flight for; the facts' Set button disables
   * when this tab's own chunk is among them. */
  readonly pendingGraphChunkIds = input<readonly string[]>([]);

  /** Whether a graph repin is in flight for this tab's chunk. */
  protected readonly graphPending = computed(() =>
    isPendingFor(this.pendingGraphChunkIds(), (id) => id === this.detail().chunk_id),
  );

  /** The graphs view's own path segments, forwarded to {@link ChunkFacts} and
   * {@link ChunkTimeline} — `null` (the default) withholds
   * the link. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** The events view's own path segments, before the `?chunk=` filter — set, the tab
   * links this chunk's own events beside its node history; `null` (the default)
   * withholds the link, since only a daemon serving an events feed has one to reach. */
  readonly eventsLinkBase = input<readonly string[] | null>(null);

  /** The issue pane's placement — `'center'` (the default) or `'inline'` for a
   * narrow host. */
  readonly issuePanePlacement = input<'center' | 'inline'>('center');

  /** Whether General includes its node-history summary. Defaults to `true`; a caller
   * with its own dedicated node-history surface opts out. */
  readonly showNodeHistory = input(true);

  /** Emitted when the operator answers an open question. */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Emitted when the operator sets a not-ready chunk's graph. */
  readonly editGraph = output<EditGraphEvent>();

  /** Emitted with a node's join key when the operator activates it in this tab's own
   * node-history summary — {@link ChunkTimeline.pickStep} forwarded straight through, a
   * pure activation signal this tab holds no selection state of its own for. */
  readonly pickStep = output<string | null>();

  protected readonly pointerCount = computed(() => this.detail().work_refs?.length ?? 0);

}
