import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { ChunkDetail } from '../../api/hub';
import { harnessName } from '../../core/harness-name';
import { injectRunnerHostNames, injectRunnerNames, runnerBoardParts } from '../../core/runner-display-name';
import { SessionEfficiency } from '../../core/session-efficiency-view';
import { injectNowSignal } from '../../core/now-signal';
import { KitAsyncState } from '../../kit/kit-async-state';
import {
  deriveActiveRow,
  deriveHistoryRows,
  deriveMultiGraph,
  type HistoryRow,
  type StepUsageTotal,
  rowChoice,
  rowMark,
  usageForStep as sumStepUsage,
} from './chunk-timeline-rows';
import { chunkGraphLabel } from './graph-label';
import { LensLink } from '../../core/lens-link';
import { toneColor } from '../../kit/kit-badge';
import { verdictTone } from './verdict-tone';

/** The chunk's node-history timeline — one row per judged node,
 * oldest-first: the node, the verdict that closed it in an aligned column
 * (`BUILD  PASS`, `REVIEW  FAIL`), and where that verdict routed the chunk —
 * capped by a synthetic row for the node currently in flight (`RUN` in cyan,
 * or the parked state's own verb), plus each step's own summed usage.
 * Row derivation ({@link HistoryRow}, {@link ActiveRow}, usage
 * summing) lives in `chunk-timeline-rows.ts` (`canon:one-owner`) — this
 * component only renders it. Presentational either way a row responds:
 * {@link activatable} makes a keyed row emit {@link ChunkTimeline.pickStep}
 (for a host already on the right route); the default,
 * non-activatable mode instead links a keyed row to the chunk detail page's
 * Node history tab under {@link linkBase} via a plain `routerLink`, with no
 * injected `Router`.
 *
 * Each `<li>` stays a bare grid item — its own listitem role is what lets a
 * screen reader announce `.timeline` as an `<ol>` with the right item count.
 * The row inside it is one interactive element: an `<a>` to the chunk page's step, a `<button>` that picks it when
 * {@link activatable}, or a plain `<div>` for a keyless row. Its content is plain text throughout — the graph badge
 * included — so no anchor nests another, and the whole row takes the native cursor, URL preview and tooltips. `.step`
 * subgrids a second time from the bare `<li>`'s own subgrid, so the verdict column stays aligned across the nesting.
 */
@Component({
  selector: 'fleet-chunk-detail-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, NgTemplateOutlet, SessionEfficiency, RouterLink, LensLink],
  templateUrl: './chunk-timeline.html',
  styleUrl: './chunk-timeline.css',
})
export class ChunkTimeline {
  /** The chunk aggregate to render (its recorded history, current node, and usage). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether to render this component's own "Node history" heading, which
   * also serves as an `aria-labelledby` target. Set `false` when the host
   * already titles the section. */
  readonly heading = input(true);

  /** Whether a row carrying a real join key activates by mouse/Enter/Space, emitting
   * {@link pickStep}, instead of linking to the chunk detail page's Node history tab
   * under {@link linkBase}. Either way, a keyed row draws the hover wash and takes the
   * focus/keyboard affordance — a keyless row (a migration, bounce, or restart, or an active row with no
   * epoch yet) draws neither, regardless of this input. `false` (the default) links;
   * a host already on the route a link would point at sets this `true`. */
  readonly activatable = input(false);

  /** The currently selected row's own key, or `null` — visual only, drawn from the
   * URL by the consumer that owns selection; this component injects no router. */
  readonly selectedKey = input<string | null>(null);

  /** The chunk detail route's own path segments, before the chunk id — the base of the link a non-activatable
   * keyed row builds to reach that node on the full chunk detail page. Unused while
   * {@link activatable} is `true`, since an activatable row emits instead of linking. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The graphs view's own path segments the heading's graph label links to; `null` leaves it plain text. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** The chunk's graph as `G-XXXX#name-YYYYMMDD`, beside the heading. */
  protected readonly graphLabel = computed(() => chunkGraphLabel(this.detail()));

  /** Emitted with an activated row's join key, or `null` when the already-selected
   * row is re-activated — the only way to clear a step selection from this component,
   * since re-navigating to an identical URL is a no-op the router drops.
   * Never emitted for a `null`-keyed row (a migration, bounce, or restart, or an active
   * row with no epoch yet) or while {@link activatable} is `false`. */
  readonly pickStep = output<string | null>();

  protected readonly rowMark = rowMark;
  protected readonly rowChoice = rowChoice;
  protected readonly verdictTone = verdictTone;
  protected readonly toneColor = toneColor;

  protected readonly chunkId = computed(() => this.detail().chunk_id);

  /** A history row's test id — `history-step` for a transition, `history-<kind>-step` otherwise. */
  protected rowTestid(row: HistoryRow): string {
    return row.kind === 'transition' ? 'history-step' : `history-${row.kind}-step`;
  }

  protected onActivate(key: string | null): void {
    if (!this.activatable() || key === null) return;
    this.pickStep.emit(key === this.selectedKey() ? null : key);
  }


  protected readonly historyRows = computed<readonly HistoryRow[]>(() => deriveHistoryRows(this.detail(), new Date(this.now())));

  /** The tick `historyRows` stamps read against, so "HH:MM" rolls over to "Yesterday". */
  private readonly now = injectNowSignal();

  /** When true, each row is labelled with its graph; a single-graph chunk shows no
   * graph badge (it would be noise). See {@link deriveMultiGraph}. */
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
  /** How many grid rows an entry takes — its main row, plus its usage line, plus a bounce's reason — which the stamp
   * cell spans. */
  protected rowSpan(row: HistoryRow): number {
    return 1 + (this.usageForStep(row) ? 1 : 0) + (row.kind === 'bounce' && row.title ? 1 : 0);
  }

  private readonly runnerHosts = injectRunnerHostNames();
  private readonly runnerNames = injectRunnerNames();

  /** Who ran a step — `xps/r-claude · claude code`: its runner as {@link runnerBoardParts} names it, then its
   * harness; either alone when the other is unrecorded, `null` when neither is. */
  protected ranBy(u: StepUsageTotal): string | null {
    let runner: string | null = null;
    if (u.runnerId !== null) {
      const parts = runnerBoardParts(u.runnerId, this.runnerNames().get(u.runnerId), this.runnerHosts().get(u.runnerId));
      runner = parts.host ? `${parts.host}/${parts.name}` : parts.name;
    }
    const harness = u.harnessId ? harnessName(u.harnessId) : null;
    return [runner, harness].filter((part) => part !== null).join(' · ') || null;
  }

  protected usageForStep(row: HistoryRow) {
    return sumStepUsage(this.detail(), row);
  }
}
