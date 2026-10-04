import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import { harnessName } from '../harness-name';
import { KitAsyncState } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import {
  deriveActiveRow,
  deriveHistoryRows,
  deriveMultiGraph,
  type HistoryRow,
  rowChoice,
  rowMark,
  usageForStep as sumStepUsage,
} from './chunk-timeline-rows';

/** The chunk's node-history timeline — one row per judged node,
 * oldest-first: the node, the verdict that closed it in an aligned column
 * (`BUILD  PASS`, `REVIEW  FAIL`), and where that verdict routed the chunk —
 * capped by a synthetic row for the node currently in flight (`RUN` in cyan,
 * or the parked state's own verb), plus each step's own summed usage.
 * Row derivation ({@link HistoryRow}, {@link ActiveRow}, usage
 * summing) lives in `chunk-timeline-rows.ts` (`canon:one-owner`) — this
 * component only renders it. Presentational either way a row responds:
 * {@link activatable} makes a keyed row emit {@link ChunkTimeline.pickStep}
 * (the chunk detail page's own tabs, already on the right route); the
 * default, non-activatable mode instead links a keyed row to the chunk
 * detail page's Node history tab under {@link linkBase} (the board dock,
 * on a different page), the same plain-`routerLink`-no-injected-`Router`
 * contract `chunk-artifacts.ts`'s own `linkBase` establishes.
 *
 * Each `<li>` stays a bare grid item — its own listitem role is what lets a
 * screen reader announce `.timeline` as an `<ol>` with the right item count.
 * The interactive `.step` div nested inside it, not the `<li>` itself, carries
 * `role="button"`/tabindex when {@link activatable} — a role set directly on
 * an `<li>` would override its implicit listitem role instead of layering on
 * top of it. Nesting works with the column layout rather than
 * against it: `.step` subgrids a second time from the bare `<li>`'s own
 * subgrid, so the verdict column stays aligned exactly as it did with one
 * subgrid level.
 *
 * A non-activatable keyed row's own link is a `.step-link` anchor stretched over
 * the whole `.step` (`position: absolute; inset: 0`), not `.step` itself made an
 * anchor — a multi-graph row already nests its own graph-badge anchor inside
 * `.step`, and an anchor cannot itself nest one. Every direct child rides above
 * the stretched link and, but for the columns that carry their own `title`
 * tooltip (the node name, the routed-to column, the recency stamp — each opts
 * back into `pointer-events: auto`, `chunk-timeline.css`), hands its own click
 * through to the link instead of claiming it — the same pattern
 * `glance-view.css`'s `.hit` establishes for a whole-row link over plain content.
 */
@Component({
  selector: 'fleet-chunk-detail-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, RouterLink],
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
   * epoch yet) draws neither, regardless of this input. `false` (the default) is the
   * board dock's own mode; the chunk detail page's own tabs, already on the route a
   * link would point at, set this `true`. No third mode today for a keyed row that is
   * neither — a consumer wanting one needs a new input, not a repurposing of this one. */
  readonly activatable = input(false);

  /** The currently selected row's own key, or `null` — visual only, drawn from the
   * URL by the consumer that owns selection; this component injects no router. */
  readonly selectedKey = input<string | null>(null);

  /** The chunk detail route's own path segments, before the chunk id — the same
   * `linkBase` contract `chunk-artifacts.ts` establishes, on the link a non-activatable
   * keyed row builds to reach that node on the full chunk detail page. Unused while
   * {@link activatable} is `true`, since an activatable row emits instead of linking. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The graphs view's own path segments, before the graph id — when set, a
   * multi-graph row's own graph badge links there (`/graphs/:graphId`), the same
   * `graphLinkBase` contract {@link ChunkFacts} already establishes for the Graph fact
   * row. `null` (the default) is every existing consumer's current behavior — plain
   * text, since the runner app (this component is shared with it) has no `/graphs`
   * route to point at. */
  readonly graphLinkBase = input<readonly string[] | null>(null);

  /** Emitted with an activated row's join key, or `null` when the already-selected
   * row is re-activated — the only way to clear a step selection from this component,
   * since re-navigating to an identical URL is a no-op the router drops.
   * Never emitted for a `null`-keyed row (a migration, bounce, or restart, or an active
   * row with no epoch yet) or while {@link activatable} is `false`. */
  readonly pickStep = output<string | null>();

  protected readonly rowMark = rowMark;
  protected readonly rowChoice = rowChoice;
  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;
  protected readonly harnessName = harnessName;

  protected readonly chunkId = computed(() => this.detail().chunk_id);

  protected onActivate(key: string | null, event?: Event): void {
    if (!this.activatable() || key === null) return;
    event?.preventDefault();
    this.pickStep.emit(key === this.selectedKey() ? null : key);
  }

  protected readonly historyRows = computed<readonly HistoryRow[]>(() => deriveHistoryRows(this.detail()));

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
  protected usageForStep(row: HistoryRow) {
    return sumStepUsage(this.detail(), row);
  }
}
