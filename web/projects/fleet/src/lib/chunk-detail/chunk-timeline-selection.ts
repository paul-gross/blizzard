import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import { deriveActiveRow, deriveHistoryRows, deriveMultiGraph, type HistoryRow, usageForStep as sumStepUsage } from './chunk-timeline-rows';

/**
 * The node history tab's own Selection list — the interactive,
 * URL-selectable half of what {@link ChunkTimeline} used to render alone: one three-line
 * card per judged node (identity; result and date; tokens and cost), the in-flight node
 * capping it, always activatable and always selectable. Both apps' Node history tabs
 * (`hub`'s and the runner's `chunk-node-history-tab.ts`) mount this component; every
 * other, read-only "what's the node history so far" composition site — the General
 * tab's own summary in both apps, and the desktop dock — stays on {@link ChunkTimeline}
 * itself, since none of them needs a selection or three stacked lines' worth of width.
 * This component is additive, not a replacement: the two diverge in layout (three
 * stacked lines here versus {@link ChunkTimeline}'s single-line grid) as well as in
 * whether a row can read `.selected`.
 *
 * Row derivation is not re-implemented: {@link deriveHistoryRows}/{@link deriveActiveRow}/
 * {@link deriveMultiGraph}/{@link sumStepUsage} (`chunk-timeline-rows.ts`) are the same
 * presentation-neutral functions {@link ChunkTimeline} itself calls, `canon:one-owner` for
 * the join-key/verdict/usage math both components render.
 *
 * Presentational only, no injection (`bzh:frontend-container-presentational`): a
 * container resolves `selectedKey` from the URL and reads {@link pickStep} back into it,
 * the same contract {@link ChunkTimeline}'s own `activatable` mode already established.
 */
@Component({
  selector: 'fleet-chunk-detail-timeline-selection',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [RouterLink],
  templateUrl: './chunk-timeline-selection.html',
  styleUrl: './chunk-timeline-selection.css',
})
export class ChunkTimelineSelection {
  /** The chunk aggregate to render (its recorded history, current node, and usage). */
  readonly detail = input.required<ChunkDetail>();

  /** The currently selected row's own key, or `null` — visual only, drawn from the URL
   * by the consumer that owns selection; this component injects no router. */
  readonly selectedKey = input<string | null>(null);

  /** The graphs view's own path segments, before the graph id — when set, a
   * multi-graph row's own graph badge links there (`/graphs/:graphId`), the same
   * `graphLinkBase` contract {@link ChunkFacts} and {@link ChunkTimeline} already
   * establish. `null` (the default) renders plain text. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Emitted with an activated row's join key, or `null` when the already-selected row
   * is re-activated — the only way to clear a step selection from this component, since
   * re-navigating to an identical URL is a no-op the router drops. Never emitted for a
   * `null`-keyed row (a migration, or an active row with no epoch yet). */
  readonly pickStep = output<string | null>();

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  protected onActivate(key: string | null, event?: Event): void {
    if (key === null) return;
    event?.preventDefault();
    this.pickStep.emit(key === this.selectedKey() ? null : key);
  }

  protected readonly historyRows = computed<readonly HistoryRow[]>(() => deriveHistoryRows(this.detail()));

  /** Whether the timeline spans more than one graph — see {@link ChunkTimeline.multiGraph}
   * for why a single-graph chunk shows no graph badge. */
  protected readonly multiGraph = computed<boolean>(() => deriveMultiGraph(this.historyRows()));

  protected readonly activeRow = computed(() => deriveActiveRow(this.detail()));

  /** One history row's summed usage, or `null` when no usage fact has landed for its
   * `(nodeId, epoch)` yet — see {@link ChunkTimeline.usageForStep}. */
  protected usageForStep(row: HistoryRow) {
    return sumStepUsage(this.detail(), row);
  }
}
