import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import {
  deriveActiveRow,
  deriveHistoryRows,
  deriveMultiGraph,
  type HistoryRow,
  usageForStep as sumStepUsage,
} from './chunk-timeline-rows';

/** The chunk's node-history timeline (issue #79) — one row per judged node,
 * oldest-first: the node, the verdict that closed it in an aligned column
 * (`BUILD  PASS`, `REVIEW  FAIL`), and where that verdict routed the chunk —
 * capped by a synthetic row for the node currently in flight (`RUN` in cyan,
 * or the parked state's own verb), plus each step's own summed usage
 * (issue #60). Row derivation ({@link HistoryRow}, {@link ActiveRow}, usage
 * summing) lives in `chunk-timeline-rows.ts` (`canon:one-owner`) — this
 * component only renders it. Presentational: {@link ChunkTimeline.pickStep}
 * emits, it does not route.
 *
 * Each `<li>` stays a bare grid item — its own listitem role is what lets a
 * screen reader announce `.timeline` as an `<ol>` with the right item count.
 * The interactive `.step` div nested inside it, not the `<li>` itself, carries
 * `role="button"`/tabindex when {@link activatable} — a role set directly on
 * an `<li>` would override its implicit listitem role instead of layering on
 * top of it (`review:F5`). Nesting works with the column layout rather than
 * against it: `.step` subgrids a second time from the bare `<li>`'s own
 * subgrid, so the verdict column stays aligned exactly as it did with one
 * subgrid level.
 */
@Component({
  selector: 'fleet-chunk-detail-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-timeline.html',
  styleUrl: './chunk-timeline.css',
})
export class ChunkTimeline {
  /** The chunk aggregate to render (its recorded history, current node, and usage). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether to render this component's own "Node history" heading. `true`
   * (the default) is kept for the dock (`chunk-detail-panel.ts`'s `.d-sec`),
   * which has no panel chrome of its own and relies on the heading both
   * visually and as its `aria-labelledby` target; a consumer already
   * wrapped in a titled `<fleet-kit-panel label="node history">`
   * (issue #205) sets this `false`. */
  readonly heading = input(true);

  /** Whether a row carrying a real join key activates by mouse/Enter/Space — gates
   * only the affordance (role/tabindex/cursor/keyboard); the hover wash stays
   * unconditional so the three composition sites render it identically regardless.
   * `false` (the default) is every existing consumer's current behavior. */
  readonly activatable = input(false);

  /** The currently selected row's own key, or `null` — visual only, drawn from the
   * URL by the consumer that owns selection; this component injects no router. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with an activated row's join key, or `null` when the already-selected
   * row is re-activated — the only way to clear a step selection from this component,
   * since re-navigating to an identical URL is a no-op the router drops
   * (`review:F6`). Never emitted for a `null`-keyed row (a migration, or an active
   * row with no epoch yet) or while {@link activatable} is `false`. */
  readonly pickStep = output<string | null>();

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  protected onActivate(key: string | null, event?: Event): void {
    if (!this.activatable() || key === null) return;
    event?.preventDefault();
    this.pickStep.emit(key === this.selectedKey() ? null : key);
  }

  protected readonly historyRows = computed<readonly HistoryRow[]>(() => deriveHistoryRows(this.detail()));

  /** Whether the timeline spans more than one graph (issue #90) — a chunk that migrated.
   * When true the board labels each row with the graph it happened in; a single-graph
   * chunk shows no graph badge (it would be noise). A migration inherently crosses two
   * graphs (its target may not yet have its own row), so its presence alone qualifies. */
  protected readonly multiGraph = computed<boolean>(() => deriveMultiGraph(this.historyRows()));

  /** The node currently in flight, as a synthetic timeline row — `RUN` while a worker
   * drives it, or the parked state's own verb (`WAITING`, `NEEDS HUMAN`, `PAUSED`).
   * Null before the chunk starts (`not_ready`/`ready`) and after it ends
   * (`done`/`stopped`): those states have no node mid-flight to report. */
  protected readonly activeRow = computed(() => deriveActiveRow(this.detail()));

  /** One history row's summed usage, or `null` when no usage fact has landed for its
   * `(nodeId, epoch)` yet — matches the row's origin node against every usage entry
   * recorded there. Multiple invocations at one step (spawn/resume/judge) fold into
   * one figure so the timeline reads one lap's cost per line. */
  protected usageForStep(row: HistoryRow) {
    return sumStepUsage(this.detail(), row);
  }
}
