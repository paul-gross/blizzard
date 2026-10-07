import { type ChunkDetail, type ChunkStatus, MigrationSource } from '../../api/hub';
import { nodeStepKey } from '../../core/node-step';
import { formatAbsolute, formatWhen } from '../../core/when';
import { bounceReason } from './parse-bounce-envelope';

/** One judged node on the timeline: the node, the verdict that closed it, and where
 * that verdict routed the chunk — a transition re-read node-first for display. A
 * `migration` step is the same shape re-read as a graph-to-graph hop: its
 * `toName` is `to_graph/landed_node`, and `graphName` labels the graph the step happened
 * in so a two-graph history is legible. `sortKey` is the raw `recorded_at` used to weave
 * transitions and migrations into one chronological timeline. {@link when}'s full-datetime
 * tooltip text lives beside it as {@link whenTitle} — the row computes the
 * view-model text once rather than the template re-deriving it from a raw instant.
 *
 * {@link key} is this step's join key ({@link nodeStepKey} of its `(nodeId, epoch)`) —
 * `null` for migrations without a worker step (non-authored sources or no origin node).
 * Authored-edge migrations join the producing step at their real epoch.
 *
 * A `bounce` row is a worker result the hub refused: `verdict` carries its cause and
 * {@link title} its envelope read as one readable line, with no node, epoch, or destination of its own. A
 * `restart` row is an operator's move of the chunk: `nodeName → toName`, by
 * {@link actor}, its graph crossing (when it left another graph) read off `toName`'s
 * `graph/node` form the way a migration's is. Neither is a node-step, so both are
 * keyless. */
export interface HistoryRow {
  readonly kind: 'transition' | 'migration' | 'bounce' | 'restart';
  readonly key: string | null;
  /** The row's epoch — `null` for a bounce, which records none. */
  readonly epoch: number | null;
  readonly nodeId: string | null;
  readonly nodeName: string;
  readonly graphName: string | null;
  /** The graph {@link graphName} names, for a consumer that links the badge to it
   * (`graphLinkBase` on the rendering components) — `null` when the row's own source
   * carries none. */
  readonly graphId: string | null;
  readonly verdict: string | null;
  /** Where the row routed the chunk — `null` for a bounce, which routed nowhere. */
  readonly toId: string | null;
  readonly toName: string | null;
  /** Who drove the row — a restart's `restarted_by`; `null` on every other kind. */
  readonly actor: string | null;
  /** The whole row's tooltip — a bounce's readable reason; `null` on every other kind. */
  readonly title: string | null;
  /** Whether the row hops graphs — a migration always, a restart when it left another
   * graph. */
  readonly crossesGraph: boolean;
  readonly when: string;
  readonly whenTitle: string;
  readonly sortKey: string;
}

/** The synthetic timeline row for the node currently in flight — see {@link deriveActiveRow}.
 * {@link key} is `null` when `latest_epoch` is unset, or when it names an epoch a landed
 * transition has already claimed — the lag window {@link deriveActiveRow} guards against. */
export interface ActiveRow {
  readonly key: string | null;
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

/** One history step's summed usage — every invocation (spawn/resume/judge)
 * recorded at that step's own `(from_node_id, epoch)`, folded into one tokens+cost
 * figure so the timeline reads one lap's cost per line. `costPartial` is true iff any
 * summed row's own wire `cost_partial` is. */
export interface StepUsageTotal {
  readonly tokens: number;
  readonly costUsd: number;
  readonly costPartial: boolean;
  /** The step's summed estimate, `null` iff no summed row carried one; already included
   * in `costUsd`. */
  readonly estimatedCostUsd: number | null;
  /** The step's own recorded harness identity — read off whichever of
   * its own summed rows recorded one, newest first, never derived from `model`. `null`
   * when no row at this step recorded a stamp (a pre-provenance row, or none at all). */
  readonly harnessId: string | null;
}

/**
 * The chunk's node-history rows, oldest-first: every judged transition, every
 * cross-graph migration, every bounce, and every restart, woven into one
 * chronological list by `recorded_at`. A cross-graph restart also records a
 * migration (`source: 'restart'`); that migration is folded into the restart's own
 * row rather than rendered as a second step for the one operator move. The single
 * owner of this derivation (`canon:one-owner`, the same precedent
 * `sort-artifacts.ts`/`transcript-steps.ts` establish for their own lists) —
 * {@link ChunkTimeline} reads it rather than re-deriving it inline.
 */
export function deriveHistoryRows(detail: ChunkDetail, now: Date): readonly HistoryRow[] {
  const transitions: HistoryRow[] = (detail.history ?? [])
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
      graphId: t.graph_id ?? null,
      verdict: t.choice_name,
      toId: t.to_node_id,
      toName: t.to_node_name ?? t.to_node_id,
      actor: null,
      title: null,
      crossesGraph: false,
      when: formatWhen(t.recorded_at, now),
      whenTitle: formatAbsolute(t.recorded_at),
      sortKey: t.recorded_at,
    }));
  // Cross-graph migration steps — the chunk left `from_graph/from_node`
  // and re-queued at `to_graph/landed_node`, woven into the same timeline by time.
  const migrations: HistoryRow[] = (detail.migrations ?? []).filter((m) => m.source !== MigrationSource.RESTART).map((m) => ({
    kind: 'migration' as const,
    key: m.source === MigrationSource.AUTHORED_EDGE && m.from_node_id !== null ? nodeStepKey(m.from_node_id, m.epoch) : null,
    epoch: m.epoch,
    nodeId: m.from_node_id,
    nodeName: m.from_node_name ?? m.from_node_id ?? '·',
    graphName: m.from_graph_name ?? m.from_graph_id,
    graphId: m.from_graph_id,
    verdict: m.choice_name ?? null,
    toId: m.landed_node_id ?? m.to_graph_id,
    toName: `${m.to_graph_name ?? m.to_graph_id}/${m.landed_node_name ?? m.landed_node_id ?? 'entry'}`,
    actor: null,
    title: null,
    crossesGraph: true,
    when: formatWhen(m.recorded_at, now),
    whenTitle: formatAbsolute(m.recorded_at),
    sortKey: m.recorded_at,
  }));
  const bounces: HistoryRow[] = (detail.bounces ?? []).map((b) => ({
    kind: 'bounce' as const,
    key: null,
    epoch: null,
    nodeId: null,
    nodeName: 'bounce',
    graphName: null,
    graphId: null,
    verdict: b.cause,
    toId: null,
    toName: null,
    actor: null,
    title: bounceReason(b.envelope),
    crossesGraph: false,
    when: formatWhen(b.recorded_at, now),
    whenTitle: formatAbsolute(b.recorded_at),
    sortKey: b.recorded_at,
  }));
  const restarts: HistoryRow[] = (detail.restarts ?? []).map((r) => {
    const toNode = r.to_node_name ?? r.to_node_id;
    const crossesGraph = r.from_graph_id != null;
    return {
      kind: 'restart' as const,
      key: null,
      epoch: r.epoch,
      nodeId: r.from_node_id ?? null,
      nodeName: r.from_node_name ?? r.from_node_id ?? '·',
      graphName: crossesGraph ? (r.from_graph_name ?? r.from_graph_id ?? null) : (r.graph_name ?? null),
      graphId: crossesGraph ? (r.from_graph_id ?? null) : r.graph_id,
      verdict: 'restart',
      toId: r.to_node_id,
      toName: crossesGraph ? `${r.graph_name ?? r.graph_id}/${toNode}` : toNode,
      actor: r.restarted_by,
      title: null,
      crossesGraph,
      when: formatWhen(r.recorded_at, now),
      whenTitle: formatAbsolute(r.recorded_at),
      sortKey: r.recorded_at,
    };
  });
  return [...transitions, ...migrations, ...bounces, ...restarts].sort((a, b) => a.sortKey.localeCompare(b.sortKey));
}

// U+FE0E pins the arrows to text presentation: bare, Chromium paints them as colour emoji
// wider than the mark column, which then overlaps the label. A bounce's mark is an inline
// SVG the templates draw, so its text mark is empty.
const KIND_MARKS: Record<Exclude<HistoryRow['kind'], 'transition'>, { mark: string; choice: string }> = {
  migration: { mark: '⤳', choice: 'migrated' },
  bounce: { mark: '', choice: 'bounced' },
  restart: { mark: '\u21BB\uFE0E', choice: 'restarted' },
};

/** The row's attempt-column text — a transition's own epoch, or its kind's glyph. */
export function rowMark(row: HistoryRow): string {
  return row.kind === 'transition' ? String(row.epoch ?? '·') : KIND_MARKS[row.kind].mark;
}

/** The row's `data-choice` key into the verdict color table — a transition's own
 * verdict, or its kind's fixed key. */
export function rowChoice(row: HistoryRow): string | null {
  return row.kind === 'transition' ? row.verdict : KIND_MARKS[row.kind].choice;
}

/** Whether `rows` spans more than one graph — a chunk that migrated. A migration or a
 * graph-crossing restart inherently crosses two graphs (its target may not yet have its
 * own row), so its presence alone qualifies. */
export function deriveMultiGraph(rows: readonly HistoryRow[]): boolean {
  if (rows.some((r) => r.crossesGraph)) return true;
  const names = new Set(rows.map((r) => r.graphName ?? ''));
  names.delete('');
  return names.size > 1;
}

/**
 * The node currently in flight, as a synthetic timeline row — `RUN` while a worker
 * drives it, or the parked state's own verb (`WAITING`, `NEEDS HUMAN`, `PAUSED`). Null
 * before the chunk starts (`not_ready`/`ready`) and after it ends (`done`/`stopped`):
 * those states have no node mid-flight to report.
 *
 * A landed transition already naming `(current_node_id, latest_epoch)` as its own
 * *destination* means `current_node_id` has moved on while `latest_epoch` (minted only
 * at the *next* lease's spawn) hasn't caught up yet — the same lag window
 * `deriveTranscriptSteps` guards against. {@link ActiveRow.key} is `null`
 * in that window rather than a key naming a step no artifact or transcript is ever
 * recorded under. Matched by `(to_node_id, epoch)`, not epoch alone: a
 * migration can hand a fresh graph an epoch a previous graph's history
 * already used, and that reuse is not this lag window.
 */
export function deriveActiveRow(detail: ChunkDetail): ActiveRow | null {
  const verb = ACTIVE_VERBS[detail.status];
  if (!verb || !detail.current_node_id) return null;
  const laggingBehind = (detail.history ?? []).some(
    (t) => t.epoch === detail.latest_epoch && t.to_node_id === detail.current_node_id,
  );
  const key =
    detail.latest_epoch !== null && !laggingBehind ? nodeStepKey(detail.current_node_id, detail.latest_epoch) : null;
  return {
    key,
    epoch: detail.latest_epoch,
    nodeId: detail.current_node_id,
    nodeName: detail.current_node_name ?? detail.current_node_id,
    ...verb,
  };
}

/** One history row's summed usage, or `null` when no usage fact has landed for its
 * `(nodeId, epoch)` yet — matches the row's origin node against every usage entry
 * recorded there. Multiple invocations at one step (spawn/resume/judge) fold into
 * one figure so the timeline reads one lap's cost per line. */
export function usageForStep(detail: ChunkDetail, row: HistoryRow): StepUsageTotal | null {
  // A restart's origin node is not a step of its own — its usage belongs to that
  // node's own transition row.
  if (!row.nodeId || row.kind === 'restart') return null;
  const rows = (detail.usage ?? []).filter((u) => u.node_id === row.nodeId && u.epoch === row.epoch);
  if (rows.length === 0) return null;
  // Newest-first: `detail.usage` arrives oldest-first, so the step's own most recent invocation is the step's own current identity.
  const stamped = [...rows].reverse().find((u) => u.harness_id != null);
  const estimatedRows = rows.flatMap((u) => (u.estimated_cost_usd == null ? [] : [u.estimated_cost_usd]));
  return {
    tokens: rows.reduce((sum, u) => sum + u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_create_tokens, 0),
    costUsd: rows.reduce((sum, u) => sum + (u.cost_usd ?? 0), 0),
    costPartial: rows.some((u) => u.cost_partial === true),
    estimatedCostUsd: estimatedRows.length > 0 ? estimatedRows.reduce((sum, amount) => sum + amount, 0) : null,
    harnessId: stamped?.harness_id ?? null,
  };
}
