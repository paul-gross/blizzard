import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import type { ChunkDetail, ChunkStatus } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import { nodeStepKey } from '../node-step';
import { formatAbsolute, formatWhen } from '../when';

/** One judged node on the timeline, a transition re-read node-first for display, or a
 * cross-graph `migration` step (issue #90) re-read as a graph-to-graph hop (`toName` is
 * `to_graph/landed_node`). `sortKey` weaves both into one chronological timeline.
 * {@link key} is this step's join key ({@link nodeStepKey} of its `(nodeId, epoch)`) —
 * `null` for a migration row, which cannot key that join (D1: synthetic `epoch: 0`,
 * nullable `nodeId`, and no artifact or transcript is ever stored under either). */
interface HistoryRow {
  readonly kind: 'transition' | 'migration';
  readonly key: string | null;
  readonly epoch: number;
  readonly nodeId: string | null;
  readonly nodeName: string;
  readonly graphName: string | null;
  readonly verdict: string | null;
  readonly toId: string;
  readonly toName: string;
  readonly when: string;
  readonly whenTitle: string;
  readonly sortKey: string;
}

/** The synthetic timeline row for the node currently in flight — see {@link ChunkTimeline.activeRow}.
 * {@link key} is null only if `latest_epoch` is unset, which an in-flight status never leaves it. */
interface ActiveRow {
  readonly key: string | null;
  readonly epoch: number | null;
  readonly nodeId: string;
  readonly nodeName: string;
  readonly choice: string;
  readonly label: string;
}

/** What the in-flight node is doing, per status — `choice` keys the verdict color table,
 * `label` is the text shown. A status absent here has no node mid-flight; no row renders. */
const ACTIVE_VERBS: Partial<Record<ChunkStatus, { choice: string; label: string }>> = {
  running: { choice: 'run', label: 'run' },
  delivering: { choice: 'run', label: 'run' },
  waiting_on_human: { choice: 'waiting', label: 'waiting' },
  needs_human: { choice: 'needs-human', label: 'needs human' },
  paused: { choice: 'paused', label: 'paused' },
};

/** One history step's summed usage (issue #60) — every invocation recorded at its own
 * `(from_node_id, epoch)`, folded into one figure so the timeline reads one lap's cost. */
interface StepUsageTotal {
  readonly tokens: number;
  readonly costUsd: number;
  readonly costPartial: boolean;
}

/** The chunk's node-history timeline (issue #79) — one row per judged node, oldest-first,
 * capped by a synthetic row for the node in flight, plus each step's own summed usage
 * (issue #60). Presentational: {@link ChunkTimeline.selectStep} emits, it does not route. */
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
          <li
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
          </li>
        }
        <!-- The node currently in flight — synthetic, not a recorded transition:
             RUN while a worker drives it, or the parked state's own verb. -->
        @if (activeRow(); as a) {
          <li
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
          </li>
        }
      </ol>
    }
  `,
  styles: `
    /* The dock has no chrome of its own, so this host pads itself; \`fleet-kit-panel\`'s
       zero-padded \`.p-body\` means a panel-wrapped consumer isn't double-padded. */
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
    /* One row per judged node. \`<ol>\` owns the track sizes; every \`<li class="step">\`
       adopts them via \`grid-template-columns: subgrid\` so the verdict column stays
       aligned regardless of node-name length. \`fit-content(160px)\` caps the node
       track's growth (a bare \`minmax(84px, max-content)\` let one long outlier name
       squeeze the verdict track to 0 width — checked, not assumed); \`.nd\`'s own
       \`min-width: 84px\` supplies the floor \`fit-content()\` doesn't. The verdict track
       is \`minmax(64px, 1fr)\`, not \`minmax(0, 1fr)\`, so it stays legible at that cap. */
    .timeline {
      display: grid;
      grid-template-columns: 16px fit-content(160px) minmax(64px, 1fr) auto;
      list-style: none;
      margin: 0;
      padding: 0;
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
    } .step:hover { border-bottom-color: var(--cyan); background: var(--tint-hover); }
    .step[role='button'] { cursor: pointer; }
    .step[role='button']:focus-visible { outline: 1px solid var(--cyan); outline-offset: -2px; }
    .step.selected { outline: 1px solid var(--cyan); outline-offset: -2px; background: var(--tint-selected); }
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
    .step[data-choice='migrated'] .verdict {
      color: var(--cyan);
    }
    .step .nd .gr {
      color: var(--label-dim);
    }
    .step[data-choice='waiting'] .verdict,
    .step[data-choice='paused'] .verdict {
      color: var(--amber-hi);
    }
    .step .jg-to {
      color: var(--label-dim);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
    }
    .step .ts {
      color: var(--label-dim);
      font-size: var(--fs-label);
      white-space: nowrap;
    }
    /* A history step's own usage — its own full-width row under the step's main cells
       (issue #182), since the node-history column is often only ~300px wide and a
       fixed column on the shared row would starve the 1fr verdict track. \`1 / -1\`
       spans all four of \`.timeline\`'s subgridded tracks; \`min-width: 0\` stops this
       spanning item's content floor from widening the column (checked, not assumed).
       flex-wrap lets the cost cell drop to its own line rather than overflow. */
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

  /** Whether to render this component's own "Node history" heading. `true` (the
   * default) is kept for the dock, which relies on it both visually and as its
   * `aria-labelledby` target; a consumer already wrapped in a titled panel (issue
   * #205) sets this `false`. */
  readonly heading = input(true);

  /** Whether a row carrying a real join key activates by mouse/Enter/Space — gates
   * only the affordance (role/tabindex/cursor/keyboard); the hover wash stays
   * unconditional so the three composition sites render it identically regardless.
   * `false` (the default) is every existing consumer's current behavior. */
  readonly activatable = input(false);

  /** The currently selected row's own key, or `null` — visual only, drawn from the
   * URL by the consumer that owns selection; this component injects no router. */
  readonly selectedKey = input<string | null>(null);

  /** Emitted with an activated row's join key — never for a `null`-keyed row (a
   * migration, or an active row with no epoch yet) or while {@link activatable} is `false`. */
  readonly selectStep = output<string>();

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  protected onActivate(key: string | null, event?: Event): void {
    if (!this.activatable() || key === null) return;
    event?.preventDefault();
    this.selectStep.emit(key);
  }

  protected readonly historyRows = computed<readonly HistoryRow[]>(() => {
    const transitions: HistoryRow[] = (this.detail().history ?? [])
      // An entry transition (no origin node) judged nothing — the node it entered
      // shows up as the next row's origin, or as the in-flight row below.
      .filter((t) => t.from_node_id)
      .map((t) => ({
        kind: 'transition' as const,
        // Non-null: the filter above already dropped every row with no from_node_id.
        key: nodeStepKey(t.from_node_id as string, t.epoch),
        epoch: t.epoch,
        nodeId: t.from_node_id,
        nodeName: t.from_node_name ?? t.from_node_id ?? '·',
        graphName: t.graph_name ?? null,
        verdict: t.choice_name,
        toId: t.to_node_id,
        toName: t.to_node_name ?? t.to_node_id,
        when: formatWhen(t.recorded_at),
        whenTitle: formatAbsolute(t.recorded_at),
        sortKey: t.recorded_at,
      }));
    // Cross-graph migration steps (issue #90) — the chunk left `from_graph/from_node`
    // and re-queued at `to_graph/landed_node`, woven into the same timeline by time.
    const migrations: HistoryRow[] = (this.detail().migrations ?? []).map((m) => ({
      kind: 'migration' as const,
      key: null, // D1: a migration's synthetic epoch/nullable nodeId cannot key the join.
      epoch: 0,
      nodeId: m.from_node_id,
      nodeName: m.from_node_name ?? m.from_node_id ?? '·',
      graphName: m.from_graph_name ?? m.from_graph_id,
      verdict: m.choice_name ?? null,
      toId: m.landed_node_id ?? m.to_graph_id,
      toName: `${m.to_graph_name ?? m.to_graph_id}/${m.landed_node_name ?? m.landed_node_id ?? 'entry'}`,
      when: formatWhen(m.recorded_at),
      whenTitle: formatAbsolute(m.recorded_at),
      sortKey: m.recorded_at,
    }));
    return [...transitions, ...migrations].sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  });

  /** Whether the timeline spans more than one graph (issue #90) — gates the per-row
   * graph badge; a migration inherently crosses two graphs, so its presence qualifies. */
  protected readonly multiGraph = computed<boolean>(() => {
    const rows = this.historyRows();
    if (rows.some((r) => r.kind === 'migration')) return true;
    const names = new Set(rows.map((r) => r.graphName ?? ''));
    names.delete('');
    return names.size > 1;
  });

  /** The node currently in flight, as a synthetic timeline row. Null before the chunk
   * starts and after it ends — those states have no node mid-flight to report. */
  protected readonly activeRow = computed<ActiveRow | null>(() => {
    const d = this.detail();
    const verb = ACTIVE_VERBS[d.status];
    if (!verb || !d.current_node_id) return null;
    return {
      key: d.latest_epoch !== null ? nodeStepKey(d.current_node_id, d.latest_epoch) : null,
      epoch: d.latest_epoch,
      nodeId: d.current_node_id,
      nodeName: d.current_node_name ?? d.current_node_id,
      ...verb,
    };
  });

  /** One history row's summed usage, `null` until a usage fact lands for its
   * `(nodeId, epoch)` — multiple invocations at one step fold into one figure. */
  protected usageForStep(row: HistoryRow): StepUsageTotal | null {
    if (!row.nodeId) return null;
    const rows = (this.detail().usage ?? []).filter((u) => u.node_id === row.nodeId && u.epoch === row.epoch);
    if (rows.length === 0) return null;
    return {
      tokens: rows.reduce(
        (sum, u) => sum + u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_create_tokens,
        0,
      ),
      costUsd: rows.reduce((sum, u) => sum + (u.cost_usd ?? 0), 0),
      costPartial: rows.some((u) => u.cost_usd === null),
    };
  }
}
