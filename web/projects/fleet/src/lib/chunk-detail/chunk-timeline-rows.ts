import type { ChunkDetail, ChunkStatus } from '../api/hub';
import { nodeStepKey } from '../node-step';
import { formatAbsolute, formatWhen } from '../when';

/** One judged node on the timeline: the node, the verdict that closed it, and where
 * that verdict routed the chunk — a transition re-read node-first for display. A
 * `migration` step (issue #90) is the same shape re-read as a graph-to-graph hop: its
 * `toName` is `to_graph/landed_node`, and `graphName` labels the graph the step happened
 * in so a two-graph history is legible. `sortKey` is the raw `recorded_at` used to weave
 * transitions and migrations into one chronological timeline. {@link when}'s full-datetime
 * tooltip text lives beside it as {@link whenTitle} (issue #175) — the row computes the
 * view-model text once rather than the template re-deriving it from a raw instant.
 *
 * {@link key} is this step's join key ({@link nodeStepKey} of its `(nodeId, epoch)`) —
 * `null` for a migration row, which cannot key that join (D1: synthetic `epoch: 0`,
 * nullable `nodeId`, and no artifact or transcript is ever stored under either). */
export interface HistoryRow {
  readonly kind: 'transition' | 'migration';
  readonly key: string | null;
  readonly epoch: number;
  readonly nodeId: string | null;
  readonly nodeName: string;
  readonly graphName: string | null;
  /** The graph {@link graphName} names, for a consumer that links the badge to it
   * (`graphLinkBase` on the rendering components) — `null` when the row's own source
   * carries none. */
  readonly graphId: string | null;
  readonly verdict: string | null;
  readonly toId: string;
  readonly toName: string;
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

/** One history step's summed usage (issue #60) — every invocation (spawn/resume/judge)
 * recorded at that step's own `(from_node_id, epoch)`, folded into one tokens+cost
 * figure so the timeline reads one lap's cost per line. */
export interface StepUsageTotal {
  readonly tokens: number;
  readonly costUsd: number;
  readonly costPartial: boolean;
}

/**
 * The chunk's node-history rows (issue #79), oldest-first: every judged transition plus
 * every cross-graph migration (issue #90), woven into one chronological list by
 * `recorded_at`. The single owner of this derivation (`canon:one-owner`, the same
 * precedent `sort-artifacts.ts`/`transcript-steps.ts` establish for their own lists) —
 * {@link ChunkTimeline} reads it rather than re-deriving it inline.
 */
export function deriveHistoryRows(detail: ChunkDetail): readonly HistoryRow[] {
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
      when: formatWhen(t.recorded_at),
      whenTitle: formatAbsolute(t.recorded_at),
      sortKey: t.recorded_at,
    }));
  // Cross-graph migration steps (issue #90) — the chunk left `from_graph/from_node`
  // and re-queued at `to_graph/landed_node`, woven into the same timeline by time.
  const migrations: HistoryRow[] = (detail.migrations ?? []).map((m) => ({
    kind: 'migration' as const,
    key: null, // D1: a migration's synthetic epoch/nullable nodeId cannot key the join.
    epoch: 0,
    nodeId: m.from_node_id,
    nodeName: m.from_node_name ?? m.from_node_id ?? '·',
    graphName: m.from_graph_name ?? m.from_graph_id,
    graphId: m.from_graph_id,
    verdict: m.choice_name ?? null,
    toId: m.landed_node_id ?? m.to_graph_id,
    toName: `${m.to_graph_name ?? m.to_graph_id}/${m.landed_node_name ?? m.landed_node_id ?? 'entry'}`,
    when: formatWhen(m.recorded_at),
    whenTitle: formatAbsolute(m.recorded_at),
    sortKey: m.recorded_at,
  }));
  return [...transitions, ...migrations].sort((a, b) => a.sortKey.localeCompare(b.sortKey));
}

/** Whether `rows` spans more than one graph (issue #90) — a chunk that migrated. When
 * true the board labels each row with the graph it happened in; a single-graph chunk
 * shows no graph badge (it would be noise). A migration inherently crosses two graphs
 * (its target may not yet have its own row), so its presence alone qualifies. */
export function deriveMultiGraph(rows: readonly HistoryRow[]): boolean {
  if (rows.some((r) => r.kind === 'migration')) return true;
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
 * `deriveTranscriptSteps` guards against (`review:F3`). {@link ActiveRow.key} is `null`
 * in that window rather than a key naming a step no artifact or transcript is ever
 * recorded under (`review:F11`). Matched by `(to_node_id, epoch)`, not epoch alone: a
 * migration (issue #90) can hand a fresh graph an epoch a previous graph's history
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
  if (!row.nodeId) return null;
  const rows = (detail.usage ?? []).filter((u) => u.node_id === row.nodeId && u.epoch === row.epoch);
  if (rows.length === 0) return null;
  return {
    tokens: rows.reduce((sum, u) => sum + u.input_tokens + u.output_tokens + u.cache_read_tokens + u.cache_create_tokens, 0),
    costUsd: rows.reduce((sum, u) => sum + (u.cost_usd ?? 0), 0),
    costPartial: rows.some((u) => u.cost_usd === null),
  };
}
