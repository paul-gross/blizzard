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
  template: `
    @if (heading()) {
      <div class="s-head"><span class="tag" id="chunk-timeline-heading">Node history</span></div>
    }
    @if (historyRows().length === 0 && !activeRow()) {
      <p class="none" data-testid="history-empty">No transitions yet — waiting on the first node-step.</p>
    } @else {
      <ol class="timeline" data-testid="history">
        @for (row of historyRows(); track $index) {
          <li class="step-item">
            <div
              class="step"
              [class.selected]="row.key !== null && row.key === selectedKey()"
              [attr.data-testid]="row.kind === 'migration' ? 'history-migration-step' : 'history-step'"
              [attr.data-choice]="row.kind === 'migration' ? 'migrated' : row.verdict"
              [attr.role]="activatable() && row.key !== null ? 'button' : null"
              [attr.tabindex]="activatable() && row.key !== null ? 0 : null"
              (click)="onActivate(row.key)"
              (keydown.enter)="onActivate(row.key, $event)"
              (keydown.space)="onActivate(row.key, $event)"
            >
              <span class="att">{{ row.kind === 'migration' ? '⤳' : row.epoch }}</span>
              <span class="nd" [attr.title]="row.nodeId">
                @if (multiGraph() && row.graphName) {
                  <span class="gr" data-testid="history-graph">{{ row.graphName }}/</span>
                }{{ row.nodeName }}</span
              >
              <!-- The judgement that closed the node, in a column of its own so the
                   verdicts read down the timeline aligned, then where it routed the
                   chunk — the fail loop's "→ build" consequence, dimmed. A migration
                   (issue #90) reads its verdict as the choice that hopped graphs and
                   routes to the target graph's landing node. -->
              <span class="jg">
                <span class="verdict" data-testid="history-choice">{{ row.verdict ?? '·' }}</span>
                <span class="jg-to" [attr.title]="row.toId">→ {{ row.toName }}</span>
              </span>
              <span class="ts" data-testid="history-when" [attr.title]="row.whenTitle || null">{{ row.when }}</span>
              <!-- That node-step's own usage (issue #60) — every invocation recorded at
                   this step's (node, epoch) summed inline, so a review-fail cycle visibly
                   shows what each lap cost. Absent when no usage fact landed for it yet. -->
              @if (usageForStep(row); as u) {
                <span class="step-usage" data-testid="history-step-usage">
                  <span class="tok" data-testid="history-step-tokens">{{ formatTokens(u.tokens) }} tok</span>
                  <span class="cost" data-testid="history-step-cost">{{ formatCost(u.costUsd, u.costPartial) }}</span>
                  @if (u.costPartial) {
                    <span
                      class="partial-badge"
                      data-testid="history-step-cost-partial"
                      title="At least one invocation's cost was absent (a crash/reap-path exit) — this step's cost is a lower bound."
                      >PARTIAL</span
                    >
                  }
                </span>
              }
            </div>
          </li>
        }
        <!-- The node currently in flight — synthetic, not a recorded transition:
             RUN while a worker drives it, or the parked state's own verb. -->
        @if (activeRow(); as a) {
          <li class="step-item">
            <div
              class="step"
              data-testid="history-active"
              [class.selected]="a.key !== null && a.key === selectedKey()"
              [attr.data-choice]="a.choice"
              [attr.role]="activatable() && a.key !== null ? 'button' : null"
              [attr.tabindex]="activatable() && a.key !== null ? 0 : null"
              (click)="onActivate(a.key)"
              (keydown.enter)="onActivate(a.key, $event)"
              (keydown.space)="onActivate(a.key, $event)"
            >
              <span class="att">{{ a.epoch ?? '·' }}</span>
              <span class="nd" [attr.title]="a.nodeId">{{ a.nodeName }}</span>
              <span class="jg">
                <span class="verdict" data-testid="history-active-verb">{{ a.label }}</span>
              </span>
            </div>
          </li>
        }
      </ol>
    }
  `,
  styles: `
    /* The dock has no chrome of its own around this component, so this host
       pads itself — \`fleet-kit-panel\`'s zero-padded \`.p-body\` (kit-panel.ts)
       means a panel-wrapped consumer isn't double-padded; the dock's own
       \`.d-sec\` (chunk-detail-panel.ts) drops its padding here to match. */
    :host {
      display: block;
      padding: 6px 8px;
    }
    .tag {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .s-head {
      margin-bottom: 6px;
    }
    .none {
      color: var(--label-dim);
      font-size: var(--fs-xs);
    }
    /* One row per judged node: the attempt, the node column, and the verdict.
       \`<ol>\` owns the track sizes; every \`<li class="step-item">\` adopts them via
       \`grid-template-columns: subgrid\` instead of sizing its own columns
       from only its own content, which can't keep the verdicts aligned once
       a column is content-sized (two rows with differently-long node names
       would size that column differently). \`.step\`, nested one level inside
       \`.step-item\` (\`review:F5\`), subgrids a second time so the same alignment
       carries through the extra level. */
    .timeline {
      display: grid;
      grid-template-columns: 16px fit-content(160px) minmax(64px, 1fr) auto;
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .step-item {
      display: grid;
      grid-column: 1 / -1;
      grid-template-columns: subgrid;
    }
    .step {
      display: grid;
      grid-column: 1 / -1;
      grid-template-columns: subgrid;
      gap: 6px;
      align-items: baseline;
      padding: 3px 0;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .step:hover {
      border-bottom-color: var(--cyan);
      background: var(--tint-hover);
    }
    .step[role='button'] {
      cursor: pointer;
    }
    .step[role='button']:focus-visible {
      outline: 1px solid var(--cyan);
      outline-offset: -2px;
    }
    .step.selected {
      outline: 1px solid var(--cyan);
      outline-offset: -2px;
      background: var(--tint-selected);
    }
    .step .att {
      color: var(--label-dim);
      font-size: var(--fs-label);
    }
    .step .nd {
      color: var(--text);
      text-transform: uppercase; /* matches every other engraved label here/in the kit */
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 84px; /* the node column's floor — see the \`.timeline\` comment above */
    }
    .step .jg {
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    /* The verdict color table — a verdict that moved the chunk on reads amber, one
       that looped it back or collided reads alarm red, and the in-flight verb reads
       cyan. Keyed on the graph's choice name; an unknown choice falls back to amber. */
    .step .verdict {
      color: var(--amber);
      white-space: nowrap;
    }
    .step[data-choice='fail'] .verdict,
    .step[data-choice='conflict'] .verdict,
    .step[data-choice='needs-human'] .verdict {
      color: var(--red);
    }
    .step[data-choice='run'] .verdict {
      color: var(--cyan);
    }
    /* A cross-graph migration (issue #90) reads cyan — a deliberate hop, not a failure. */
    .step[data-choice='migrated'] .verdict {
      color: var(--cyan);
    }
    /* The graph a step happened in, shown only on a two-graph (migrated) timeline. */
    .step .nd .gr {
      color: var(--label-dim);
    }
    .step[data-choice='waiting'] .verdict,
    .step[data-choice='paused'] .verdict {
      color: var(--amber-hi);
    }
    /* Where the verdict routed the chunk — a consequence, so it reads dim. */
    .step .jg-to {
      color: var(--label-dim);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    /* When the judgement landed — recency at a glance, right-aligned and dim. */
    .step .ts {
      color: var(--label-dim);
      font-size: var(--fs-label);
      white-space: nowrap;
    }
    /* A history step's own usage — its own full-width row under the step's main
       cells (issue #182), not squeezed onto the same line as the verdict/timestamp.
       The node-history column is one third of the detail panel (chunk-detail-panel.ts's
       three-way split) — often only ~300px wide, sometimes narrower still — so
       reserving a fixed column on the *shared* row starves the 1fr verdict track and
       clips or overlaps neighboring cells. Spanning the full row width instead gives
       this line the whole column's width, usually comfortable for the two
       fixed-width cells inside it. min-width: 0 stops this spanning item from
       forcing the row wider than the column actually has (grid's default is to let a
       spanning item's own content floor win); flex-wrap: wrap is the fallback for
       whatever's left over — the token cell and the cost cell each keep their own
       nowrap (a figure's own text never breaks), but the pair as a whole can drop
       the cost onto its own line rather than overflow the column. \`1 / -1\`
       still spans all four of \`.timeline\`'s subgridded tracks under \`.step\`;
       checked (not assumed) that \`min-width: 0\` keeps this spanning item
       from contributing back into their shared sizing. */
    .step-usage {
      grid-column: 1 / -1;
      min-width: 0;
      display: flex;
      flex-wrap: wrap;
      gap: 2px 6px;
      margin-top: 2px;
      color: var(--label);
      font-size: var(--fs-xs);
    }
    .step-usage .tok {
      flex: 0 0 70px;
      white-space: nowrap;
    }
    .step-usage .cost {
      flex: 0 0 56px;
      text-align: right;
      white-space: nowrap;
    }
    /* The PARTIAL badge marks a cost total whose sum is a lower bound (issue #60). */
    .partial-badge {
      margin-left: 4px;
      padding: 0 4px;
      border: 1px solid var(--red-dim);
      color: var(--red);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      cursor: help;
    }
  `,
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
