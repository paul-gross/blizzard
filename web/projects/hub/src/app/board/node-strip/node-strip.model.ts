import { type ChunkStatus, type GraphView, type RunnerBoardParts, STATUS_LANE, type TransitionView } from 'fleet';

/** A strip block's reading of the chunk's visits to one node. */
export type NodeBlockState = 'current' | 'passed' | 'failed' | 'unvisited';

/** One drawn block: the node it stands for and how the chunk's history left it. */
export interface NodeBlock {
  readonly nodeId: string;
  readonly name: string;
  readonly state: NodeBlockState;
}

/** Blocks per group — the strip breaks into tens, as a subscription's window blocks do. */
export const NODE_STRIP_GROUP = 10;

/**
 * The graph's nodes, start to finish: each node ranked by its longest forward path from the entry, ties kept in
 * discovery order. A forward edge is any edge a depth-first walk from the entry does not close back onto a node
 * still on its path, so a rework loop (`fail` → `build`) never pushes its target later. Nodes the entry never
 * reaches follow, in the graph's own order.
 */
export function orderGraphNodes(graph: GraphView): readonly string[] {
  const idByName = new Map(graph.nodes.map((n) => [n.name, n.node_id]));
  const out = new Map<string, string[]>();
  for (const node of graph.nodes) out.set(node.node_id, []);
  for (const edge of graph.edges) {
    const to = edge.target_kind === 'node' ? idByName.get(edge.to_node_name) : undefined;
    if (to !== undefined) out.get(edge.from_node_id)?.push(to);
  }

  const discovery: string[] = [];
  const onPath = new Set<string>();
  const seen = new Set<string>();
  const forward = new Map<string, string[]>();
  const walk = (id: string): void => {
    seen.add(id);
    onPath.add(id);
    discovery.push(id);
    const next: string[] = [];
    for (const to of out.get(id) ?? []) {
      if (onPath.has(to)) continue;
      next.push(to);
      if (!seen.has(to)) walk(to);
    }
    forward.set(id, next);
    onPath.delete(id);
  };
  if (out.has(graph.entry_node_id)) walk(graph.entry_node_id);

  // Longest forward path, relaxed until stable: the forward edges form a DAG, so this terminates.
  const depth = new Map(discovery.map((id) => [id, 0]));
  let changed = true;
  while (changed) {
    changed = false;
    for (const id of discovery) {
      for (const to of forward.get(id) ?? []) {
        const d = depth.get(id)! + 1;
        if (d > depth.get(to)!) {
          depth.set(to, d);
          changed = true;
        }
      }
    }
  }

  const index = new Map(discovery.map((id, i) => [id, i]));
  const reached = [...discovery].sort((a, b) => depth.get(a)! - depth.get(b)! || index.get(a)! - index.get(b)!);
  const unreached = graph.nodes.map((n) => n.node_id).filter((id) => !seen.has(id));
  return [...reached, ...unreached];
}

/** One completed visit, as the strip reads it: the node left, where it went next, and when. */
/** The fields of a migration step the strip reads — a re-pin leaves its node on a choice, as a transition does. */
interface MigrationStep {
  readonly epoch?: number | null;
  readonly from_node_name?: string | null;
  readonly landed_node_name?: string | null;
}

interface Visit {
  readonly epoch: number;
  readonly from: string | null | undefined;
  readonly to: string | null | undefined;
}

/**
 * The strip's blocks, one per graph node in {@link orderGraphNodes} order. Visits are matched to nodes by name, not
 * id: node ids are minted per graph version, so a chunk that followed its graph onto a newer version carries
 * history under the old version's ids. The visits are the chunk's transitions together with its migrations (a
 * re-pin leaves its node on a choice just as a transition does), taken in epoch order; a step whose node name is
 * not one of this graph's is skipped. A visit failed when it routed back to the node itself or to one earlier in the
 * order — a rework or bounce — and passed otherwise; only the last visit to a node counts. The node the chunk
 * stands on now is `current` whatever its history.
 */
export function nodeBlocks(
  graph: GraphView,
  history: readonly TransitionView[],
  migrations: readonly MigrationStep[],
  currentNodeId: string | null,
): readonly NodeBlock[] {
  const order = orderGraphNodes(graph);
  const nameById = new Map(graph.nodes.map((n) => [n.node_id, n.name]));
  const rank = new Map(order.map((id, i) => [nameById.get(id), i]));
  const visits: Visit[] = [
    ...history.map((step) => ({ epoch: step.epoch, from: step.from_node_name, to: step.to_node_name })),
    ...migrations.map((m) => ({ epoch: m.epoch ?? 0, from: m.from_node_name, to: m.landed_node_name })),
  ].sort((a, b) => a.epoch - b.epoch);

  const failedByName = new Map<string, boolean>();
  for (const visit of visits) {
    const from = visit.from ? rank.get(visit.from) : undefined;
    if (!visit.from || from === undefined) continue;
    const to = visit.to ? rank.get(visit.to) : undefined;
    failedByName.set(visit.from, to !== undefined && to <= from);
  }
  return order.map((nodeId) => {
    const name = nameById.get(nodeId) ?? nodeId;
    const failed = failedByName.get(name);
    const state: NodeBlockState =
      nodeId === currentNodeId ? 'current' : failed === undefined ? 'unvisited' : failed ? 'failed' : 'passed';
    return { nodeId, name, state };
  });
}

/** The blocks broken into groups of {@link NODE_STRIP_GROUP}. */
export function groupBlocks(blocks: readonly NodeBlock[]): readonly (readonly NodeBlock[])[] {
  const groups: NodeBlock[][] = [];
  for (let i = 0; i < blocks.length; i += NODE_STRIP_GROUP) groups.push(blocks.slice(i, i + NODE_STRIP_GROUP));
  return groups;
}

/** Whether a chunk carries the strip: only in the lanes where it is mid-graph — RUNNING, WAIT/HUMAN, NEEDS HUMAN. */
export function showsNodeStrip(chunk: { readonly status: ChunkStatus; readonly terminal: boolean }): boolean {
  const lane = STATUS_LANE(chunk.status);
  return !chunk.terminal && (lane === 'running' || lane === 'waiting' || lane === 'needs');
}

/** A step label naming its graph — `adv-dwf/build` — or the bare label until the graph's name is known. Only a node
 * label takes the prefix: a status word standing in its place (`stopped`) stays bare. */
export function graphStepLabel(graphName: string | null | undefined, node: string, label: string = node): string {
  return graphName && label === node ? `${graphName}/${label}` : label;
}

/** A holding runner as the board's cards write it: `xps/r-claude`, or the bare name with no host. */
export function runnerBoardLabel(runner: RunnerBoardParts): string {
  return runner.host ? `${runner.host}/${runner.name}` : runner.name;
}

/** The separator between a card's step and its holding runner — the mobile card's own glyph. */
export const STEP_RUNNER_SEP = ' · ';

/** The strip's grouped blocks once both its graph and the chunk's detail are read — empty until then, so the strip
 * draws nothing rather than a half-lit row. */
export function stripGroups(
  graph: GraphView | undefined,
  detail: { readonly history?: readonly TransitionView[]; readonly migrations?: readonly MigrationStep[] } | undefined,
  currentNodeId: string | null,
): readonly (readonly NodeBlock[])[] {
  if (!graph || !detail) return [];
  return groupBlocks(nodeBlocks(graph, detail.history ?? [], detail.migrations ?? [], currentNodeId));
}

/** Past twenty blocks the strip packs tighter, so each still reads. */
export function isDenseStrip(groups: readonly (readonly NodeBlock[])[]): boolean {
  return groups.reduce((n, g) => n + g.length, 0) > 20;
}
