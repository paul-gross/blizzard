import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ChunkDetail, ChunkStatus } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import { formatWhen } from '../when';

/** One judged node on the timeline: the node, the verdict that closed it, and where
 * that verdict routed the chunk — a transition re-read node-first for display. A
 * `migration` step (issue #90) is the same shape re-read as a graph-to-graph hop: its
 * `toName` is `to_graph/landed_node`, and `graphName` labels the graph the step happened
 * in so a two-graph history is legible. `sortKey` is the raw `recorded_at` used to weave
 * transitions and migrations into one chronological timeline. */
interface HistoryRow {
  readonly kind: 'transition' | 'migration';
  readonly epoch: number;
  readonly nodeId: string | null;
  readonly nodeName: string;
  readonly graphName: string | null;
  readonly verdict: string | null;
  readonly toId: string;
  readonly toName: string;
  readonly when: string;
  readonly sortKey: string;
}

/** The synthetic timeline row for the node currently in flight — see {@link ChunkTimeline.activeRow}. */
interface ActiveRow {
  readonly epoch: number | null;
  readonly nodeId: string;
  readonly nodeName: string;
  readonly choice: string;
  readonly label: string;
}

/** What the in-flight node is doing, per status — `choice` keys the verdict color
 * table in the styles (run reads cyan, the parked verbs amber-hi/red), `label` is the
 * text shown. Statuses absent here have no node mid-flight, so no row renders. */
const ACTIVE_VERBS: Partial<Record<ChunkStatus, { choice: string; label: string }>> = {
  running: { choice: 'run', label: 'run' },
  delivering: { choice: 'run', label: 'run' },
  waiting_on_human: { choice: 'waiting', label: 'waiting' },
  needs_human: { choice: 'needs-human', label: 'needs human' },
  paused: { choice: 'paused', label: 'paused' },
};

/** One history step's summed usage (issue #60) — every invocation (spawn/resume/judge)
 * recorded at that step's own `(from_node_id, epoch)`, folded into one tokens+cost
 * figure so the timeline reads one lap's cost per line. */
interface StepUsageTotal {
  readonly tokens: number;
  readonly costUsd: number;
  readonly costPartial: boolean;
}

/**
 * The chunk's node-history timeline (issue #79) — one row per judged node,
 * oldest-first: the node, the verdict that closed it in an aligned column
 * (`BUILD  PASS`, `REVIEW  FAIL`), and where that verdict routed the chunk —
 * capped by a synthetic row for the node currently in flight (`RUN` in cyan,
 * or the parked state's own verb), plus each step's own summed usage
 * (issue #60). Presentational only.
 */
@Component({
  selector: 'fleet-chunk-detail-timeline',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="s-head"><span class="tag">Node history</span></div>
    @if (historyRows().length === 0 && !activeRow()) {
      <p class="none" data-testid="history-empty">No transitions yet — waiting on the first node-step.</p>
    } @else {
      <ol class="timeline" data-testid="history">
        @for (row of historyRows(); track $index) {
          <li
            class="step"
            [attr.data-testid]="row.kind === 'migration' ? 'history-migration-step' : 'history-step'"
            [attr.data-choice]="row.kind === 'migration' ? 'migrated' : row.verdict"
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
            <span class="ts" data-testid="history-when">{{ row.when }}</span>
            <!-- That node-step's own usage (issue #60) — every invocation recorded at
                 this step's (node, epoch) summed inline, so a review-fail cycle visibly
                 shows what each lap cost. Absent when no usage fact landed for it yet. -->
            @if (usageForStep(row); as u) {
              <span class="step-usage" data-testid="history-step-usage">
                <span data-testid="history-step-tokens">{{ formatTokens(u.tokens) }} tok</span>
                <span data-testid="history-step-cost">{{ formatCost(u.costUsd, u.costPartial) }}</span>
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
          <li class="step" data-testid="history-active" [attr.data-choice]="a.choice">
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
    :host {
      display: block;
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
    /* One row per judged node: the attempt, the node in a fixed column, and the
       verdict — fixed widths so the verdicts read down the timeline aligned. */
    .timeline {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .step {
      display: grid;
      grid-template-columns: 16px 84px 1fr auto;
      gap: 6px;
      align-items: baseline;
      padding: 3px 0;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-sm);
      line-height: 1.5;
    }
    .step .att {
      color: var(--label-dim);
      font-size: var(--fs-label);
    }
    .step .nd {
      color: var(--text);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      min-width: 0;
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
    /* A history step's own usage — tucked onto the same line as its judgement choice. */
    .step-usage {
      display: flex;
      gap: 6px;
      color: var(--label);
      font-size: var(--fs-xs);
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

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  protected readonly historyRows = computed<readonly HistoryRow[]>(() => {
    const transitions: HistoryRow[] = (this.detail().history ?? [])
      // An entry transition (no origin node) judged nothing — the node it entered
      // shows up as the next row's origin, or as the in-flight row below.
      .filter((t) => t.from_node_id)
      .map((t) => ({
        kind: 'transition' as const,
        epoch: t.epoch,
        nodeId: t.from_node_id,
        nodeName: t.from_node_name ?? t.from_node_id ?? '·',
        graphName: t.graph_name ?? null,
        verdict: t.choice_name,
        toId: t.to_node_id,
        toName: t.to_node_name ?? t.to_node_id,
        when: formatWhen(t.recorded_at),
        sortKey: t.recorded_at,
      }));
    // Cross-graph migration steps (issue #90) — the chunk left `from_graph/from_node`
    // and re-queued at `to_graph/landed_node`, woven into the same timeline by time.
    const migrations: HistoryRow[] = (this.detail().migrations ?? []).map((m) => ({
      kind: 'migration' as const,
      epoch: 0,
      nodeId: m.from_node_id,
      nodeName: m.from_node_name ?? m.from_node_id ?? '·',
      graphName: m.from_graph_name ?? m.from_graph_id,
      verdict: m.choice_name ?? null,
      toId: m.landed_node_id ?? m.to_graph_id,
      toName: `${m.to_graph_name ?? m.to_graph_id}/${m.landed_node_name ?? m.landed_node_id ?? 'entry'}`,
      when: formatWhen(m.recorded_at),
      sortKey: m.recorded_at,
    }));
    return [...transitions, ...migrations].sort((a, b) => a.sortKey.localeCompare(b.sortKey));
  });

  /** Whether the timeline spans more than one graph (issue #90) — a chunk that migrated.
   * When true the board labels each row with the graph it happened in; a single-graph
   * chunk shows no graph badge (it would be noise). A migration inherently crosses two
   * graphs (its target may not yet have its own row), so its presence alone qualifies. */
  protected readonly multiGraph = computed<boolean>(() => {
    const rows = this.historyRows();
    if (rows.some((r) => r.kind === 'migration')) return true;
    const names = new Set(rows.map((r) => r.graphName ?? ''));
    names.delete('');
    return names.size > 1;
  });

  /** The node currently in flight, as a synthetic timeline row — `RUN` while a worker
   * drives it, or the parked state's own verb (`WAITING`, `NEEDS HUMAN`, `PAUSED`).
   * Null before the chunk starts (`not_ready`/`ready`) and after it ends
   * (`done`/`stopped`): those states have no node mid-flight to report. */
  protected readonly activeRow = computed<ActiveRow | null>(() => {
    const d = this.detail();
    const verb = ACTIVE_VERBS[d.status];
    if (!verb || !d.current_node_id) return null;
    return {
      epoch: d.latest_epoch,
      nodeId: d.current_node_id,
      nodeName: d.current_node_name ?? d.current_node_id,
      ...verb,
    };
  });

  /** One history row's summed usage, or `null` when no usage fact has landed for its
   * `(nodeId, epoch)` yet — matches the row's origin node against every usage entry
   * recorded there. Multiple invocations at one step (spawn/resume/judge) fold into
   * one figure so the timeline reads one lap's cost per line. */
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
